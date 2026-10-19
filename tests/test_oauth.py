import base64
from dataclasses import replace
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

from argon2 import PasswordHasher
import httpx2
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.testclient import TestClient

from memhub.auth import OAuthProvider
from memhub.permissions import PermissionPolicy
from memhub.server import create_app
from test_http import live_server, payload


VERIFIER = 'test-verifier-' + 'a' * 48
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip('=')
CALLBACK = 'https://client.example/callback'


async def authorize(client, config, identity='claude', password='test-oauth-password', auth_method='none', omit_scope=False, omit_resource=False):
    config.env_path.write_text(config.env_path.read_text() + "OAUTH_PASSWORD_HASH='" +
                               PasswordHasher().hash('test-oauth-password') + "'\n")
    registered = await client.post('/register', json={
        'redirect_uris': [CALLBACK], 'client_name': 'Synthetic integration client',
        'token_endpoint_auth_method': auth_method,
        'grant_types': ['authorization_code', 'refresh_token'], 'response_types': ['code'],
        'scope': 'memory offline_access',
    })
    assert registered.status_code == 201, registered.text
    registration = registered.json()
    params = {
        'client_id': registration['client_id'], 'response_type': 'code', 'redirect_uri': CALLBACK,
        'code_challenge': CHALLENGE, 'code_challenge_method': 'S256',
        'state': 'client-state', 'resource': config.public_url + '/mcp', 'scope': 'memory offline_access',
    }
    if omit_scope:
        del params['scope']
    if omit_resource:
        del params['resource']
    response = await client.get('/authorize', params=params)
    assert response.status_code == 302, response.text
    location = urlsplit(response.headers['location'])
    ticket = parse_qs(location.query)['ticket'][0]
    page = await client.get(location.path + '?' + location.query)
    assert page.status_code == 200
    assert 'Which app is this' in page.text
    assert all(app in page.text for app in config.oauth_apps)
    response = await client.post('/oauth/login', data={'ticket': ticket, 'identity': identity, 'password': password})
    return registration, response, ticket


def exchange_data(registration, response, config):
    query = parse_qs(urlsplit(response.headers['location']).query)
    assert query['state'] == ['client-state']
    assert query['iss'] == [config.public_url]
    return {'grant_type': 'authorization_code', 'client_id': registration['client_id'],
            'code': query['code'][0], 'redirect_uri': CALLBACK, 'code_verifier': VERIFIER,
            'resource': config.public_url + '/mcp'}


@pytest.mark.parametrize('identity', ['claude', 'chatgpt', 'gemini', 'grok', 'grok-bot', 'codex'])
async def test_dcr_pkce_login_mcp_identity_refresh_rotation_and_persistence(live_server, config, identity):
    async with httpx2.AsyncClient(base_url=live_server, follow_redirects=False) as client:
        registration, response, _ = await authorize(client, config, identity)
        assert response.status_code == 302
        data = exchange_data(registration, response, config)
        token_response = await client.post('/token', data=data)
        assert token_response.status_code == 200, token_response.text
        tokens = token_response.json()
        assert (await client.post('/token', data=data)).status_code == 400  # one-use code
        async with httpx2.AsyncClient(headers={'Authorization': 'Bearer ' + tokens['access_token']}) as mcp_client:
            async with streamable_http_client(live_server + '/mcp', http_client=mcp_client) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    context = await session.call_tool('get_context')
                    assert not context.is_error
                    remembered = await session.call_tool('remember', {'path': 'topics/social-media.md',
                        'fact': '长期关注科普文章', 'description': '文案偏好'})
                    if identity == 'grok':
                        assert remembered.is_error
                    else:
                        assert not remembered.is_error
                        item = payload(await session.call_tool('read', {'paths': ['topics/social-media.md']}))['files'][0]
                        assert '[' + identity + ' · ' in item['content']
        persisted = OAuthProvider(config, PermissionPolicy(config.clients_path))
        try:
            loaded = await persisted.load_access_token(tokens['access_token'])
            assert loaded.subject == identity
            rows = persisted.clients_list()
            assert rows[0]['identity'] == identity and rows[0]['last_issued_at']
        finally:
            persisted.close()
        refreshed = await client.post('/token', data={
            'grant_type': 'refresh_token', 'client_id': registration['client_id'],
            'refresh_token': tokens['refresh_token'], 'resource': config.public_url + '/mcp',
        })
        assert refreshed.status_code == 200, refreshed.text
        new_tokens = refreshed.json()
        assert new_tokens['access_token'] != tokens['access_token']
        assert new_tokens['refresh_token'] != tokens['refresh_token']
        assert (await client.get('/mcp', headers={'Authorization': 'Bearer ' + tokens['access_token']})).status_code == 401
        replay = await client.post('/token', data={'grant_type': 'refresh_token', 'client_id': registration['client_id'],
            'refresh_token': tokens['refresh_token'], 'resource': config.public_url + '/mcp'})
        assert replay.status_code == 400
        assert replay.json()['error'] == 'invalid_grant'
        persisted = OAuthProvider(config, PermissionPolicy(config.clients_path))
        try:
            persisted.revoke_client(registration['client_id'])
        finally:
            persisted.close()
        assert (await client.get('/mcp', headers={'Authorization': 'Bearer ' + new_tokens['access_token']})).status_code == 401
        revoked_refresh = await client.post('/token', data={'grant_type': 'refresh_token', 'client_id': registration['client_id'],
            'refresh_token': new_tokens['refresh_token'], 'resource': config.public_url + '/mcp'})
        assert revoked_refresh.status_code == 401


async def test_bad_password_identity_and_pkce_are_rejected(live_server, config):
    async with httpx2.AsyncClient(base_url=live_server, follow_redirects=False) as client:
        registration, response, ticket = await authorize(client, config, password='wrong-password')
        assert response.status_code == 403
        assert not config.access_log_path.exists()
        response = await client.post('/oauth/login', data={'ticket': ticket, 'identity': 'intruder', 'password': 'test-oauth-password'})
        assert response.status_code == 403
        response = await client.post('/oauth/login', data={'ticket': ticket, 'identity': 'claude', 'password': 'test-oauth-password'})
        data = exchange_data(registration, response, config)
        wrong = await client.post('/token', data={**data, 'code_verifier': 'wrong-verifier'})
        assert wrong.status_code == 400 and wrong.json()['error'] == 'invalid_grant'
        wrong_resource = await client.post('/token', data={**data, 'resource': 'https://different.example/mcp'})
        assert wrong_resource.status_code == 400 and wrong_resource.json()['error'] == 'invalid_target'
        assert (await client.post('/token', data=data)).status_code == 200


def test_public_metadata_login_401_host_and_forwarded_proto(config):
    config = replace(config, public_url='https://mem.example.com')
    with TestClient(create_app(config), base_url=config.public_url) as client:
        metadata = client.get('/.well-known/oauth-protected-resource/mcp').json()
        assert client.get('/.well-known/oauth-protected-resource').json() == metadata
        assert metadata['resource'] == config.public_url + '/mcp'
        assert metadata['authorization_servers'] == [config.public_url]
        assert metadata['scopes_supported'] == ['memory']
        auth = client.get('/.well-known/oauth-authorization-server').json()
        assert auth['issuer'] == config.public_url
        for field, path in [('authorization_endpoint', '/authorize'), ('token_endpoint', '/token'),
                            ('registration_endpoint', '/register'), ('revocation_endpoint', '/revoke')]:
            assert auth[field] == config.public_url + path
        assert auth['code_challenge_methods_supported'] == ['S256']
        assert 'none' in auth['token_endpoint_auth_methods_supported']
        assert 'offline_access' in auth['scopes_supported']
        assert not auth.get('client_id_metadata_document_supported', False)
        denied = client.get('/mcp', headers={'X-Forwarded-Proto': 'https'})
        assert denied.status_code == 401
        assert f'resource_metadata="{config.public_url}/.well-known/oauth-protected-resource/mcp"' in denied.headers['www-authenticate']
        page = client.get('/oauth/login')
        assert page.status_code == 200 and 'type="password"' in page.text
        assert config.public_url + '/oauth/login' in page.text
        response = client.post('/mcp', headers={'Authorization': 'Bearer long-test-token', 'X-Forwarded-Proto': 'https'},
            json={'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
                'protocolVersion': '2025-11-25', 'capabilities': {}, 'clientInfo': {'name': 'manual-test', 'version': '1'}}})
        assert response.status_code == 200
        bad_host = client.post('/mcp', headers={'Host': 'evil.example', 'Authorization': 'Bearer long-test-token'}, json={})
        assert bad_host.status_code == 421


def test_rate_limit_and_unknown_static_identity(config):
    config = replace(config, rate_limit_per_minute=2)
    config.env_path.write_text('STATIC_TOKENS=\'{"known":"claude-code","unknown":"intruder","other":"gemini-cli"}\'\n')
    with TestClient(create_app(config)) as client:
        assert client.get('/mcp', headers={'Authorization': 'Bearer unknown'}).status_code == 401
        for _ in range(2):
            assert client.get('/mcp', headers={'Authorization': 'Bearer known'}).status_code != 429
        limited = client.get('/mcp', headers={'Authorization': 'Bearer known'})
        assert limited.status_code == 429 and int(limited.headers['retry-after']) > 0
        assert client.get('/mcp', headers={'Authorization': 'Bearer other'}).status_code != 429


async def test_log_only_identity_tool_paths_no_content(live_server, config):
    secret_fact = '测试事实不可进入访问日志'
    async with httpx2.AsyncClient(headers={'Authorization': 'Bearer long-test-token'}) as client:
        async with streamable_http_client(live_server + '/mcp', http_client=client) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                await session.call_tool('remember', {'path': 'topics/log.md', 'fact': secret_fact, 'description': '日志测试'})
                await session.call_tool('search', {'query': secret_fact})
                await session.call_tool('read', {'paths': ['topics/log.md']})
    log = config.access_log_path.read_text()
    assert secret_fact not in log
    assert 'long-test-token' not in log
    rows = [json.loads(line) for line in log.splitlines()]
    assert [row['tool'] for row in rows] == ['remember', 'search', 'read']
    assert all(set(row) == {'time', 'identity', 'tool', 'paths'} for row in rows)
    assert rows[0]['identity'] == 'claude-code'
    assert rows[0]['paths'] == ['topics/log.md']
    assert rows[1]['paths'] == ['topics/log.md']


@pytest.mark.parametrize('auth_method', ['none', 'client_secret_post', 'client_secret_basic'])
async def test_optional_scope_client_auth_and_sdk_revocation(live_server, config, auth_method):
    async with httpx2.AsyncClient(base_url=live_server, follow_redirects=False) as client:
        registration, response, _ = await authorize(client, config, auth_method=auth_method, omit_scope=True)
        data = exchange_data(registration, response, config)
        headers = {}
        if auth_method == 'client_secret_post':
            data['client_secret'] = registration['client_secret']
        elif auth_method == 'client_secret_basic':
            data.pop('client_id')
            headers['Authorization'] = 'Basic ' + base64.b64encode(
                (registration['client_id'] + ':' + registration['client_secret']).encode()).decode()
        result = await client.post('/token', data=data, headers=headers)
        assert result.status_code == 200, result.text
        tokens = result.json()
        assert 'memory' in tokens['scope'].split()
        assert (await client.post('/mcp', headers={'Authorization': 'Bearer ' + tokens['access_token']},
            json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list', 'params': {}})).status_code == 200
        revoke = {'token': tokens['refresh_token'], 'client_id': registration['client_id']}
        if auth_method == 'client_secret_post':
            revoke['client_secret'] = registration['client_secret']
        elif auth_method == 'client_secret_basic':
            revoke.pop('client_id')
        response = await client.post('/revoke', data=revoke, headers=headers)
        assert response.status_code == 200, response.text
        assert (await client.get('/mcp', headers={'Authorization': 'Bearer ' + tokens['access_token']})).status_code == 401


async def test_expired_code_access_refresh_and_invalid_authorize_target(live_server, config):
    async with httpx2.AsyncClient(base_url=live_server, follow_redirects=False) as client:
        registration, response, _ = await authorize(client, config)
        data = exchange_data(registration, response, config)
        provider = OAuthProvider(config, PermissionPolicy(config.clients_path))
        try:
            with provider.db:
                provider.db.execute("UPDATE codes SET data=json_set(data,'$.expires_at',0)")
            expired_code = await client.post('/token', data=data)
            assert expired_code.status_code == 400 and expired_code.json()['error'] == 'invalid_grant'
        finally:
            provider.close()
        registration, response, _ = await authorize(client, config)
        tokens = (await client.post('/token', data=exchange_data(registration, response, config))).json()
        provider = OAuthProvider(config, PermissionPolicy(config.clients_path))
        try:
            with provider.db:
                provider.db.execute("UPDATE tokens SET data=json_set(data,'$.expires_at',1)")
        finally:
            provider.close()
        assert (await client.get('/mcp', headers={'Authorization': 'Bearer ' + tokens['access_token']})).status_code == 401
        refresh = await client.post('/token', data={'grant_type': 'refresh_token', 'client_id': registration['client_id'],
            'refresh_token': tokens['refresh_token'], 'resource': config.public_url + '/mcp'})
        assert refresh.status_code == 400 and refresh.json()['error'] == 'invalid_grant'
        rejected = await client.get('/authorize', params={'client_id': registration['client_id'], 'response_type': 'code',
            'redirect_uri': CALLBACK, 'code_challenge': CHALLENGE, 'resource': 'https://other.example/mcp'})
        assert parse_qs(urlsplit(rejected.headers['location']).query)['error'] == ['invalid_target']


async def test_mcp_permissions_do_not_expose_people_or_areas(live_server, config):
    from memhub.storage import MemoryStore

    store = MemoryStore(config)
    try:
        for path in ['people/alice.md', 'areas/work.md', 'topics/social-media.md']:
            store.remember(path, '长期关注科普分享', 'claude-code', description='分享')
    finally:
        store.close()
    async with httpx2.AsyncClient(base_url=live_server, follow_redirects=False) as client:
        registration, response, _ = await authorize(client, config, identity='grok')
        tokens = (await client.post('/token', data=exchange_data(registration, response, config))).json()
    async with httpx2.AsyncClient(headers={'Authorization': 'Bearer ' + tokens['access_token']}) as client:
        async with streamable_http_client(live_server + '/mcp', http_client=client) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                denied = await session.call_tool('read', {'paths': ['people/alice.md']})
                assert denied.is_error and 'Permission denied' in str(denied)
                context = payload(await session.call_tool('get_context'))
                assert all(not path.startswith(('people/', 'areas/')) for path in context['files'])
                hits = payload(await session.call_tool('search', {'query': '科普'}))['hits']
                assert hits and all(row['path'] == 'topics/social-media.md' for row in hits)
                denied = await session.call_tool('remember', {'path': 'areas/new.md', 'fact': '希望管理项目', 'description': '项目'})
                assert denied.is_error and 'Permission denied' in str(denied)
    async with httpx2.AsyncClient(base_url=live_server, follow_redirects=False) as client:
        registration, response, _ = await authorize(client, config, identity='chatgpt')
        tokens = (await client.post('/token', data=exchange_data(registration, response, config))).json()
    async with httpx2.AsyncClient(headers={'Authorization': 'Bearer ' + tokens['access_token']}) as client:
        async with streamable_http_client(live_server + '/mcp', http_client=client) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                written = await session.call_tool('remember', {'path': 'areas/new.md', 'fact': '希望管理项目', 'description': '项目'})
                assert not written.is_error and payload(written)['status'] == 'written'


async def test_omitted_resource_is_bound_to_this_endpoint(live_server, config):
    async with httpx2.AsyncClient(base_url=live_server, follow_redirects=False) as client:
        registration, response, _ = await authorize(client, config, 'gemini', omit_resource=True)
        assert response.status_code == 302
        data = exchange_data(registration, response, config)
        del data['resource']
        tokens = await client.post('/token', data=data)
        assert tokens.status_code == 200, tokens.text
        refresh = await client.post('/token', data={'grant_type': 'refresh_token', 'client_id': registration['client_id'],
            'refresh_token': tokens.json()['refresh_token']})
        assert refresh.status_code == 200, refresh.text
        access = refresh.json()['access_token']
        listed = await client.post('/mcp', headers={'Authorization': 'Bearer ' + access, 'Accept': 'application/json, text/event-stream'},
            json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list', 'params': {}})
        assert listed.status_code == 200, listed.text
