"""Privacy boundaries use synthetic records only; never load the real workbook."""
import asyncio
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
def private_catalog(tmp_path):
    source = tmp_path / 'catalog'
    source.mkdir()
    samples = {
        'mentors': [{
            'id': 'sample-person', 'name': '虚构导师', 'title': '样例导师',
            'description': '这是虚构的计算机领域导师。', 'industry': '信息技术',
            'tags': ['计算机'], 'has_digital_mentor': True, 'available': False,
        }],
        'activities': [{
            'id': 'sample-activity', 'title': '虚构活动', 'status': 'open',
            'start_offset_days': 5, 'deadline_offset_days': 4,
        }],
        'documents': [{
            'id': 'sample-document', 'mentor_id': 'sample-person',
            'title': '样例经验', 'text': '虚构导师分享的计算机项目经验。',
        }],
    }
    real = {
        'id': 'mentor-cohort6-synthetic', 'title': '张导师 · 001',
        'display_name': '张导师 · 001', 'subtitle': '信息技术 · 技术研发',
        'description': '概括职业背景为技术研发，工作城市为北京。',
        'industry': '信息技术', 'city': '北京', 'organization_type': '企业',
        'role_family': '技术研发', 'education_domains': ['计算机'],
        'tags': ['信息技术', '计算机'], 'privacy_level': 'deidentified',
        'has_digital_mentor': False, 'available': False,
        'booking_method': '预约信息尚未接入，请通过项目运营方确认。',
        # Canary values deliberately represent source columns and bookkeeping.
        'name': 'PRIVATE_FULL_NAME_CANARY', 'account': 'PRIVATE_ACCOUNT_CANARY',
        'password': 'PRIVATE_PASSWORD_CANARY', 'phone': 'PRIVATE_PHONE_CANARY',
        'email': 'PRIVATE_EMAIL_CANARY', 'company': 'PRIVATE_COMPANY_CANARY',
        'exact_job_title': 'PRIVATE_JOB_CANARY', 'volunteer': 'PRIVATE_VOLUNTEER_CANARY',
        'source_row': 'PRIVATE_ROW_CANARY',
    }
    (source / 'sample_catalog.json').write_text(json.dumps(samples, ensure_ascii=False))
    (source / 'imported_mentors.json').write_text(json.dumps({'mentors': [real]}, ensure_ascii=False))
    return Catalog(source, reference_date=date(2026, 11, 7))


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / 'runtime', mode='sample',
                    access_code='synthetic-test-code', session_secret='synthetic-secret',
                    web_search_enabled=True)


def assert_public(value):
    serialized = json.dumps(value, ensure_ascii=False)
    assert 'PRIVATE_' not in serialized
    return serialized


def test_real_directory_replaces_samples_but_explicit_sample_session_survives(private_catalog):
    resources = private_catalog.public_resources()
    assert [m['id'] for m in resources['mentors']] == ['mentor-cohort6-synthetic']
    assert all(not m['is_sample'] for m in resources['mentors'])
    assert resources['activities'][0]['is_sample']
    summary = private_catalog.data_summary()
    assert summary['real_mentor_count'] == 1
    assert summary['sample_mentor_count'] == 0
    assert summary['mentor_documents_count'] == 0
    assert summary['privacy_mode'] == 'deidentified'
    old = private_catalog.mentor('sample-person')
    assert old['is_sample'] is True
    assert '虚构' in old['source_label']
    docs = private_catalog.search_experience('', mentor_id='sample-person')
    assert docs and all(doc['is_sample'] for doc in docs)


def test_no_fictional_experience_fallback_for_real_mentor(private_catalog):
    assert private_catalog.search_experience('计算机') == []
    assert private_catalog.search_experience('计算机', mentor_id='mentor-cohort6-synthetic') == []
    assert private_catalog.search_experience('', mentor_id='unknown') == []


def test_public_projection_and_tool_payload_strip_private_columns(private_catalog, settings):
    assert_public(private_catalog.public_resources())
    assert_public(private_catalog.mentor('mentor-cohort6-synthetic'))
    assert_public(private_catalog.find_mentors('计算机'))

    async def run():
        async with httpx.AsyncClient() as client:
            agent = Agent(settings, private_catalog, client)
            payload, cards, citations = await agent.execute_tool('find_mentors', '{"query":"计算机"}', None)
            assert cards and not citations
            assert_public(payload)
            assert 'PRIVATE_' not in agent.prompt('mentor-cohort6-synthetic')
    asyncio.run(run())


def test_http_projection_auth_and_no_fake_digital_mentor(private_catalog, settings):
    with TestClient(create_app(settings, catalog=private_catalog)) as client:
        assert client.get('/api/resources').status_code == 401
        client.post('/api/login', json={'code': settings.access_code}).raise_for_status()
        response = client.get('/api/resources')
        assert response.status_code == 200
        assert_public(response.json())
        # Neither arbitrary project paths nor the source spreadsheet are served.
        for path in ['/第六期导师总表.xlsx', '/data/imported_mentors.json', '/runtime/mentor_source_map.json']:
            assert client.get(path).status_code == 404
        result = client.post('/api/sessions', json={'mentor_id': 'mentor-cohort6-synthetic'})
        assert result.status_code == 400
        assert client.post('/api/sessions', json={'mentor_id': 'sample-person'}).status_code == 200


@pytest.mark.parametrize('query', [
    '张导师 · 001', '张导师的真实姓名', '查询导师电话', '校友导师邮箱',
    '微信联系方式 校友', '还原导师真实身份',
])
def test_reverse_identification_search_never_calls_network(private_catalog, settings, query):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={'results': []})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            agent = Agent(settings, private_catalog, client)
            result, cards, citations = await agent.execute_tool('web_search', json.dumps({'query': query}), None)
            assert result.get('error')
            assert cards == citations == []
    asyncio.run(run())
    assert requests == []


def test_general_career_search_remains_available(private_catalog, settings):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={'results': []})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            agent = Agent(settings, private_catalog, client)
            result, _, _ = await agent.execute_tool('web_search', '{"query":"计算机专业 职业发展 公开资源"}', None)
            assert 'error' not in result
    asyncio.run(run())
    assert len(requests) == 1
