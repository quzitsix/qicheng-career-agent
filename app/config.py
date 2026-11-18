from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path

DEMO_ROOT = Path(__file__).resolve().parents[1]


def private_file(path: Path, initial: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        try:
            with path.open('x') as output:
                output.write(initial + '\n')
        except FileExistsError:
            pass
    path.chmod(0o600)
    return path.read_text().strip()


def read_api_key(path: Path) -> str:
    supplied = os.environ.get('DEEPSEEK_API_KEY', '').strip()
    if supplied:
        return supplied
    if not path.is_file():
        return ''
    # Accept a raw key, a JSON value, or an environment assignment; never log it.
    matches = re.findall(r'\bsk-[A-Za-z0-9_-]{10,}', path.read_text())
    if len(set(matches)) == 1:
        return matches[0]
    raise ValueError('API Key 文件无法识别或包含多个密钥，请设置 DEEPSEEK_API_KEY。')


@dataclass
class Settings:
    data_dir: Path
    api_key: str = field(repr=False, default='')
    model: str = 'deepseek-flash'
    mode: str = 'deepseek'
    access_code: str = field(repr=False, default='')
    session_secret: str = field(repr=False, default='')
    web_search_enabled: bool = True
    max_rounds: int = 5

    @classmethod
    def load(cls) -> 'Settings':
        data_dir = Path(os.environ.get('SRT_DATA_DIR', str(DEMO_ROOT / 'runtime'))).resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        data_dir.chmod(0o700)
        mode = os.environ.get('SRT_MODE', 'deepseek')
        if mode not in {'sample', 'deepseek'}:
            raise ValueError('SRT_MODE 必须是 deepseek 或 sample')
        key = '' if mode == 'sample' else read_api_key(Path(os.environ.get('DEEPSEEK_API_KEY_FILE', str(DEMO_ROOT.parent / 'api_key'))))
        if mode == 'deepseek' and not key:
            raise ValueError('未找到 DeepSeek API Key；配置密钥，或设置 SRT_MODE=sample 体验离线界面。')
        configured_code = os.environ.get('SRT_ACCESS_CODE', '').strip()
        if configured_code and len(configured_code) < 8:
            raise ValueError('SRT_ACCESS_CODE 至少需要 8 个字符。')
        code = configured_code or private_file(data_dir / 'access_code', secrets.token_urlsafe(9))
        secret = private_file(data_dir / 'session_secret', secrets.token_hex(32))
        return cls(data_dir=data_dir, api_key=key, model=os.environ.get('DEEPSEEK_MODEL', 'deepseek-flash'), mode=mode,
                   access_code=code, session_secret=secret,
                   web_search_enabled=mode == 'deepseek' and os.environ.get('SRT_WEB_SEARCH', '1') == '1')
