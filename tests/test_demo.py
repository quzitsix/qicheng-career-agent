import json
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agent import Agent
from app.catalog import Catalog
from app.config import Settings
from app.main import create_app


@pytest.fixture
def sample_catalog(tmp_path):
    directory = tmp_path / "sample-data"
    directory.mkdir()
    # Synthetic records belong to the test case; no deployment data is required.
    synthetic = {
        "mentors": [
            {"id": "fixture-mentor-a", "title": "测试导师甲", "description": "虚构测试角色", "has_digital_mentor": True},
            {"id": "fixture-mentor-b", "title": "测试导师乙", "description": "虚构测试角色", "has_digital_mentor": True},
        ],
        "activities": [
            {"id": "fixture-active", "title": "测试活动", "status": "open", "start_offset_days": 5, "deadline_offset_days": 4},
            {"id": "fixture-ended", "title": "过期测试活动", "status": "open", "start_offset_days": -4, "deadline_offset_days": -5},
            {"id": "fixture-closed", "title": "截止测试活动", "status": "open", "start_offset_days": 3, "deadline_offset_days": -1},
        ],
        "documents": [
            {"id": "fixture-document-a", "mentor_id": "fixture-mentor-a", "title": "测试经验甲", "text": "仅用于自动测试的虚构资料甲。"},
            {"id": "fixture-document-b", "mentor_id": "fixture-mentor-b", "title": "测试经验乙", "text": "仅用于自动测试的虚构资料乙。"},
        ],
    }
    (directory / "sample_catalog.json").write_text(json.dumps(synthetic, ensure_ascii=False), encoding="utf-8")
    return Catalog(directory, reference_date=date(2026, 11, 6))


@pytest.fixture
def cfg(tmp_path):
    return Settings(data_dir=tmp_path, mode='sample', access_code='test-invite-123', session_secret='test-signing-secret', web_search_enabled=False)


def test_auth_isolation_stream_and_persistence(cfg, sample_catalog):
    app = create_app(cfg, catalog=sample_catalog)
    with TestClient(app) as alice, TestClient(app) as bob:
        assert alice.get('/api/resources').status_code == 401
        assert alice.post('/api/login', json={'code': 'wrong'}).status_code == 401
        assert alice.post('/api/login', json={'code': '错误邀请码'}).status_code == 401
        for user in (alice, bob):
            assert user.post('/api/login', json={'code': cfg.access_code}).status_code == 200
        session = alice.post('/api/sessions', json={}).json()
        assert bob.get('/api/sessions/' + session['id']).status_code == 404
        assert bob.post('/api/chat', json={'session_id': session['id'], 'message': '读取历史'}).status_code == 404
        response = alice.post('/api/chat', json={'session_id': session['id'], 'message': '推荐近期活动'})
        events = [json.loads(line) for line in response.text.splitlines()]
        assert events[-1]['type'] == 'done'
        assert any(e['type'] == 'resources' for e in events)
        assert '虚构' in ''.join(e.get('text', '') for e in events)
        history = alice.get('/api/sessions/' + session['id']).json()['messages']
        assert [m['role'] for m in history] == ['user', 'assistant']
        assert history[-1]['resources']
        assert bob.get('/api/sessions').json() == {'sessions': []}
        cookie = alice.cookies.get('srt_demo_session')
        alice.cookies.set('srt_demo_session', '0' * 48 + '.' + cookie.split('.')[1])
        assert alice.get('/api/sessions').status_code == 401


def test_http_boundary(cfg, sample_catalog):
    with TestClient(create_app(cfg, catalog=sample_catalog)) as client:
        assert client.post('/api/login', json={'code': cfg.access_code}, headers={'Origin': 'https://unrelated.example'}).status_code == 403
        assert client.get('/api_key').status_code == 404
        assert client.get('/static/../runtime/access_code').status_code == 404
        response = client.get('/api/status')
        assert 'secret' not in response.text and cfg.access_code not in response.text
        assert response.headers['Cache-Control'] == 'no-store'
        client.post('/api/login', json={'code': cfg.access_code})
        assert client.post('/api/sessions', json={'mentor_id': 'missing'}).status_code == 400
        session = client.post('/api/sessions', json={}).json()
        assert client.post('/api/chat', json={'session_id': session['id'], 'message': '   '}).status_code == 400


def test_activity_expiry_and_mentor_scope(sample_catalog):
    catalog = sample_catalog
    all_items = catalog.public_resources()['activities']
    result = catalog.find_activities()
    assert any(item['status'] == 'ended' for item in all_items)
    assert all(item['status'] not in {'ended', 'closed', 'cancelled'} for item in result)
    mentors = catalog.public_resources()['mentors']
    for mentor in mentors:
        docs = catalog.search_experience(query='', mentor_id=mentor['id'])
        assert all(doc['mentor_id'] == mentor['id'] for doc in docs)
    assert catalog.search_experience(query='', mentor_id='does-not-exist') == []
    assert all(item['start_at'] and item['start_at'][:10] >= '2026-11-10' for item in catalog.find_activities(date_from='2026-11-10'))


def sse(packets):
    return ''.join('data: ' + json.dumps(packet) + '\n\n' for packet in packets) + 'data: [DONE]\n\n'


def test_actual_agent_loop_protocol_and_forced_scope(cfg, sample_catalog):
    import asyncio
    cfg.mode = 'deepseek'
    cfg.api_key = 'test-not-a-real-key'
    catalog = sample_catalog
    mentors = catalog.public_resources()['mentors']
    fixed, other = mentors[0]['id'], mentors[1]['id']
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            arguments = json.dumps({'query': '', 'mentor_id': other})
            return httpx.Response(200, text=sse([
                {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'call_123', 'function': {'name': 'search_mentor_experience', 'arguments': arguments[:12]}}]}}]},
                {'choices': [{'delta': {'tool_calls': [{'index': 0, 'function': {'arguments': arguments[12:]}}]}, 'finish_reason': 'tool_calls'}]},
            ]))
        assert payload['messages'][-2]['tool_calls'][0]['id'] == 'call_123'
        assert payload['messages'][-1]['tool_call_id'] == 'call_123'
        passages = json.loads(payload['messages'][-1]['content'])['passages']
        assert passages and all(item['mentor_id'] == fixed for item in passages)
        return httpx.Response(200, text=sse([
            {'choices': [{'delta': {'content': '依据该导师的样例资料回答。'}}]},
            {'choices': [{'delta': {}, 'finish_reason': 'stop'}]},
        ]))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            agent = Agent(cfg, catalog, client)
            return [event async for event in agent.run([{'role': 'user', 'content': '分享导师经验'}], fixed)]
    events = asyncio.run(run())
    assert len(requests) == 2
    assert requests[0]['thinking'] == {'type': 'disabled'}
    assert any(event['type'] == 'citations' for event in events)
    assert any(event.get('text') == '依据该导师的样例资料回答。' for event in events)
