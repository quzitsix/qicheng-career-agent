from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import Agent, ModelError
from .catalog import Catalog
from .config import DEMO_ROOT, Settings
from .storage import Store

COOKIE = 'srt_demo_session'


class LoginInput(BaseModel):
    code: str = Field(min_length=1, max_length=128)


class SessionInput(BaseModel):
    mentor_id: str | None = Field(default=None, max_length=100)


class ChatInput(BaseModel):
    session_id: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=4000)


def create_app(settings: Settings | None = None, catalog=None) -> FastAPI:
    cfg = settings or Settings.load()
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    store = Store(cfg.data_dir / 'sessions.sqlite3')
    resources = catalog or Catalog(DEMO_ROOT / 'data')
    active_sessions: set[str] = set()
    request_times: dict[str, deque] = defaultdict(deque)

    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(timeout=httpx.Timeout(75, connect=15), limits=httpx.Limits(max_connections=12)) as client:
            app.state.agent = Agent(cfg, resources, client)
            yield

    app = FastAPI(title='启程 · 学生职业发展 Demo', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.settings = store, cfg

    def limited(key: str, count: int, seconds: int):
        current = time.monotonic()
        queue = request_times[key]
        while queue and queue[0] < current - seconds:
            queue.popleft()
        if len(queue) >= count:
            raise HTTPException(429, '请求较多，请稍后再试。')
        queue.append(current)
        # Expire inactive buckets so a shared test does not grow unbounded.
        if len(request_times) > 2048:
            for old_key in list(request_times):
                if not request_times[old_key] or request_times[old_key][-1] < current - 3600:
                    del request_times[old_key]

    def owner_from_cookie(request: Request):
        token = request.cookies.get(COOKIE, '')
        parts = token.split('.')
        if len(parts) != 2 or not re.fullmatch(r'[a-f0-9]{48}', parts[0]) or not re.fullmatch(r'[a-f0-9]{64}', parts[1]):
            return None
        owner, signature = parts
        expected = hmac.new(cfg.session_secret.encode(), owner.encode(), hashlib.sha256).hexdigest()
        return owner if hmac.compare_digest(expected, signature) else None

    def require_owner(request: Request):
        owner = owner_from_cookie(request)
        if not owner:
            raise HTTPException(401, '请输入测试邀请码。')
        return owner

    @app.middleware('http')
    async def guard(request: Request, call_next):
        from fastapi.responses import JSONResponse
        if request.method in {'POST', 'PUT', 'DELETE', 'PATCH'}:
            origin = request.headers.get('origin')
            if origin and urlsplit(origin).netloc != request.headers.get('host'):
                return JSONResponse({'detail': '不允许跨站操作。'}, status_code=403)
            try:
                if int(request.headers.get('content-length', '0')) > 65536:
                    return JSONResponse({'detail': '请求过大。'}, status_code=413)
            except ValueError:
                return JSONResponse({'detail': '无效请求。'}, status_code=400)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/api/status')
    def status(request: Request):
        summary = resources.data_summary()
        return {'mode': cfg.mode, 'model': cfg.model if cfg.mode == 'deepseek' else '离线规则演示',
                'sample_data': bool(summary['sample_mentor_count'] or summary['sample_activity_count']),
                'data_summary': summary, 'authenticated': bool(owner_from_cookie(request)),
                'web_search_enabled': cfg.web_search_enabled}

    @app.post('/api/login')
    def login(body: LoginInput, request: Request, response: Response):
        limited('login:' + (request.client.host if request.client else 'unknown'), 12, 60)
        if not hmac.compare_digest(body.code.strip().encode('utf-8'), cfg.access_code.encode('utf-8')):
            raise HTTPException(401, '邀请码不正确。')
        owner = owner_from_cookie(request) or secrets.token_hex(24)
        signature = hmac.new(cfg.session_secret.encode(), owner.encode(), hashlib.sha256).hexdigest()
        response.set_cookie(COOKIE, owner + '.' + signature, httponly=True, samesite='lax',
                            secure=request.url.scheme == 'https', max_age=7 * 86400, path='/')
        return {'ok': True}

    @app.get('/api/resources')
    def resource_list(owner=Depends(require_owner)):
        return resources.public_resources()

    @app.get('/api/sessions')
    def sessions(owner=Depends(require_owner)):
        return {'sessions': store.list(owner)}

    @app.post('/api/sessions')
    def new_session(body: SessionInput, owner=Depends(require_owner)):
        limited('session:' + owner, 30, 600)
        if body.mentor_id:
            mentor = resources.mentor(body.mentor_id)
            if not mentor or not mentor.get('has_digital_mentor'):
                raise HTTPException(400, '该导师尚未配置数字导师。')
        return store.create(owner, body.mentor_id)

    @app.get('/api/sessions/{session_id}')
    def session(session_id: str, owner=Depends(require_owner)):
        selected = store.session(session_id, owner)
        if not selected:
            raise HTTPException(404, '未找到该会话。')
        return {'session': selected, 'messages': store.messages(session_id)}

    @app.post('/api/chat')
    async def chat(body: ChatInput, request: Request, owner=Depends(require_owner)):
        message = body.message.strip()
        if not message:
            raise HTTPException(400, '请输入问题。')
        selected = store.session(body.session_id, owner)
        if not selected:
            raise HTTPException(404, '未找到该会话。')
        if body.session_id in active_sessions:
            raise HTTPException(409, '该会话正在回复，请稍后发送。')
        if len(active_sessions) >= 4:
            raise HTTPException(429, '演示服务正在处理其他请求，请稍后重试。')
        limited('chat:' + owner, 30, 600)
        history = store.messages(body.session_id)
        if len(history) >= 100:
            raise HTTPException(400, '本轮会话已较长，请开启新对话。')
        active_sessions.add(body.session_id)
        store.add(body.session_id, 'user', message)
        history.append({'role': 'user', 'content': message})

        async def stream():
            collected_text = ''
            cards, citations = {}, {}
            state = 'interrupted'
            try:
                async with asyncio.timeout(150):
                    async for event in app.state.agent.run(history, selected['mentor_id']):
                        kind = event['type']
                        if kind == 'delta':
                            collected_text += event['text']
                        elif kind == 'resources':
                            for card in event['items']:
                                cards[card['id']] = card
                        elif kind == 'citations':
                            for citation in event['items']:
                                citations[citation.get('url') or citation['id']] = citation
                        yield json.dumps(event, ensure_ascii=False) + '\n'
                state = 'complete'
                message_id = store.add(body.session_id, 'assistant', collected_text, list(cards.values()), list(citations.values()))
                yield json.dumps({'type': 'done', 'message_id': message_id}) + '\n'
            except (ModelError, TimeoutError) as exc:
                state = 'error'
                error = str(exc) if isinstance(exc, ModelError) else '本次查询耗时较长，请缩小问题后重试。'
                collected_text += '\n\n' + error
                yield json.dumps({'type': 'error', 'message': error}, ensure_ascii=False) + '\n'
            except asyncio.CancelledError:
                raise
            except Exception:
                state = 'error'
                error = '处理请求时遇到问题，请重新尝试；管理员可检查本地服务。'
                collected_text += '\n\n' + error
                yield json.dumps({'type': 'error', 'message': error}, ensure_ascii=False) + '\n'
            finally:
                if state != 'complete':
                    text = collected_text.strip() or '本次回复已中断，可以继续提问。'
                    store.add(body.session_id, 'assistant', text, list(cards.values()), list(citations.values()), status=state)
                active_sessions.discard(body.session_id)

        return StreamingResponse(stream(), media_type='application/x-ndjson', headers={'X-Accel-Buffering': 'no'})

    @app.get('/favicon.ico', include_in_schema=False)
    def favicon():
        return Response(status_code=204)

    @app.get('/')
    def index():
        return FileResponse(DEMO_ROOT / 'web' / 'index.html')

    app.mount('/static', StaticFiles(directory=DEMO_ROOT / 'web'), name='static')
    return app
