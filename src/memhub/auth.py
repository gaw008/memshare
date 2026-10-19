"""Persistent single-user OAuth provider; SDK handles protocol routes and PKCE."""

import json
import secrets
import sqlite3
import time
from urllib.parse import urlsplit
from html import escape
from threading import RLock

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from dotenv import dotenv_values
from mcp.server.auth.provider import (
    AccessToken, AuthorizationCode, AuthorizationParams, AuthorizeError,
    OAuthAuthorizationServerProvider, RefreshToken, TokenError, construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

from memhub.config import Config, load_tokens
from memhub.permissions import PermissionPolicy


class OAuthProvider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    def __init__(self, config: Config, permissions: PermissionPolicy):
        self.config = config
        self.permissions = permissions
        self.resource = config.public_url + '/mcp'
        self._lock = RLock()
        config.oauth_db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(config.oauth_db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self.db:
            self.db.executescript('''
                CREATE TABLE IF NOT EXISTS clients (
                    client_id TEXT PRIMARY KEY, metadata TEXT NOT NULL,
                    identity TEXT, last_issued_at INTEGER, revoked INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS pending (
                    ticket TEXT PRIMARY KEY, client_id TEXT NOT NULL, params TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS codes (code TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tokens (
                    token TEXT PRIMARY KEY, kind TEXT NOT NULL, client_id TEXT NOT NULL,
                    family TEXT NOT NULL, data TEXT NOT NULL);
            ''')
        config.oauth_db_path.chmod(0o600)

    def close(self):
        self.db.close()

    async def get_client(self, client_id):
        with self._lock:
            row = self.db.execute('SELECT metadata FROM clients WHERE client_id=? AND revoked=0', (client_id,)).fetchone()
        return OAuthClientInformationFull.model_validate_json(row['metadata']) if row else None

    async def register_client(self, client_info):
        with self._lock, self.db:
            self.db.execute('INSERT INTO clients(client_id,metadata) VALUES (?,?)',
                            (client_info.client_id, client_info.model_dump_json()))

    async def authorize(self, client, params):
        # Clients that omit the resource indicator are bound to this single MCP endpoint.
        if params.resource is None:
            params.resource = self.resource
        if params.resource != self.resource:
            raise AuthorizeError('invalid_target', 'resource must identify this MCP endpoint')
        if params.scopes is None:
            params.scopes = (client.scope or "").split()
        ticket = secrets.token_urlsafe(32)
        with self._lock, self.db:
            self.db.execute('INSERT INTO pending VALUES (?,?,?,?)',
                            (ticket, client.client_id, params.model_dump_json(),
                             time.time() + self.config.authorization_code_ttl_seconds))
        return self.config.public_url + '/oauth/login?ticket=' + ticket

    def _pending(self, ticket):
        return self.db.execute('SELECT * FROM pending WHERE ticket=? AND expires>?', (ticket, time.time())).fetchone()

    async def login(self, request: Request):
        if request.method == 'GET':
            ticket = request.query_params.get('ticket', '')
            pending = self._pending(ticket) if ticket else None
            if ticket and not pending:
                return JSONResponse({'error': 'Invalid or expired login request'}, status_code=400)
            redirect_host = escape(urlsplit(str(AuthorizationParams.model_validate_json(pending['params']).redirect_uri)).netloc) if pending else '请从 app 发起授权'
            options = ''.join(f'<option value="{escape(app)}">{escape(app)}</option>' for app in self.config.oauth_apps)
            response = HTMLResponse(f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
                <meta name="viewport" content="width=device-width, initial-scale=1"><title>memhub 授权</title>
                <body><main><h1>授权个人记忆库</h1><p>授权后返回：{redirect_host}</p><p>选择此连接使用的 app 身份和权限。</p>
                <form method="post" action="{escape(self.config.public_url)}/oauth/login">
                <input type="hidden" name="ticket" value="{escape(ticket)}">
                <label>口令 <input name="password" type="password" required autocomplete="current-password"></label>
                <label>这是哪个 app <select name="identity">{options}</select></label>
                <button type="submit">授权</button></form></main></body></html>''',
                headers={'Cache-Control': 'no-store'})
            if ticket:
                response.set_cookie('memhub_login', ticket, httponly=True, samesite='lax',
                                    secure=self.config.public_url.startswith('https:'),
                                    max_age=self.config.authorization_code_ttl_seconds, path='/oauth/login')
            return response
        form = await request.form()
        ticket = str(form.get('ticket', ''))
        identity = str(form.get('identity', ''))
        if not ticket or request.cookies.get('memhub_login') != ticket:
            return JSONResponse({'error': 'Invalid login request'}, status_code=400)
        password_hash = dotenv_values(self.config.env_path, interpolate=False).get('OAUTH_PASSWORD_HASH')
        if not password_hash:
            return JSONResponse({'error': 'OAuth password is not configured; run set-password'}, status_code=503)
        try:
            PasswordHasher().verify(password_hash, str(form.get('password', '')))
        except VerifyMismatchError:
            return JSONResponse({'error': 'Incorrect password'}, status_code=403)
        if identity not in self.config.oauth_apps or identity not in self.permissions.identities:
            return JSONResponse({'error': 'Unknown app identity'}, status_code=403)
        with self._lock, self.db:
            pending = self._pending(ticket)
            if not pending:
                return JSONResponse({'error': 'Invalid or expired login request'}, status_code=400)
            client = self.db.execute('SELECT * FROM clients WHERE client_id=? AND revoked=0', (pending['client_id'],)).fetchone()
            if not client or (client['identity'] and client['identity'] != identity):
                return JSONResponse({'error': 'Client identity cannot be changed; register a new client'}, status_code=403)
            params = AuthorizationParams.model_validate_json(pending['params'])
            code = AuthorizationCode(code=secrets.token_urlsafe(32), client_id=pending['client_id'],
                                     scopes=params.scopes or [], expires_at=time.time() + self.config.authorization_code_ttl_seconds,
                                     code_challenge=params.code_challenge, redirect_uri=params.redirect_uri,
                                     redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
                                     resource=params.resource, subject=identity)
            self.db.execute('UPDATE clients SET identity=? WHERE client_id=?', (identity, code.client_id))
            self.db.execute('INSERT INTO codes VALUES (?,?)', (code.code, code.model_dump_json()))
            self.db.execute('DELETE FROM pending WHERE ticket=?', (ticket,))
        response = RedirectResponse(construct_redirect_uri(str(params.redirect_uri), code=code.code,
                                                          state=params.state, iss=self.config.public_url), status_code=302)
        response.headers['Cache-Control'] = 'no-store'
        response.delete_cookie('memhub_login', path='/oauth/login')
        return response

    async def load_authorization_code(self, client, authorization_code):
        with self._lock:
            row = self.db.execute('SELECT data FROM codes WHERE code=?', (authorization_code,)).fetchone()
        return AuthorizationCode.model_validate_json(row['data']) if row else None

    def _issue(self, client_id, identity, scopes, resource):
        if identity not in self.permissions.identities:
            raise TokenError('invalid_grant', 'Unknown or unbound identity')
        now = int(time.time())
        family = secrets.token_urlsafe(32)
        access = AccessToken(token=secrets.token_urlsafe(32), client_id=client_id, scopes=scopes,
                             expires_at=now + self.config.access_token_ttl_seconds, resource=resource, subject=identity)
        refresh = RefreshToken(token=secrets.token_urlsafe(32), client_id=client_id, scopes=scopes,
                               expires_at=now + self.config.refresh_token_ttl_seconds, resource=resource, subject=identity)
        for kind, token in [('access', access), ('refresh', refresh)]:
            self.db.execute('INSERT INTO tokens VALUES (?,?,?,?,?)',
                            (token.token, kind, client_id, family, token.model_dump_json()))
        self.db.execute('UPDATE clients SET last_issued_at=? WHERE client_id=?', (now, client_id))
        return OAuthToken(access_token=access.token, token_type='Bearer', expires_in=self.config.access_token_ttl_seconds,
                          refresh_token=refresh.token, scope=' '.join(scopes))

    async def exchange_authorization_code(self, client, authorization_code):
        with self._lock, self.db:
            removed = self.db.execute('DELETE FROM codes WHERE code=?', (authorization_code.code,))
            if removed.rowcount != 1:
                raise TokenError('invalid_grant', 'Authorization code already used')
            return self._issue(client.client_id, authorization_code.subject, authorization_code.scopes, authorization_code.resource)

    async def load_refresh_token(self, client, refresh_token):
        with self._lock:
            row = self.db.execute('SELECT data FROM tokens WHERE token=? AND kind=? AND client_id=?',
                                  (refresh_token, 'refresh', client.client_id)).fetchone()
        return RefreshToken.model_validate_json(row['data']) if row else None

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        with self._lock, self.db:
            row = self.db.execute('SELECT family FROM tokens WHERE token=? AND kind=?', (refresh_token.token, 'refresh')).fetchone()
            if not row:
                raise TokenError('invalid_grant', 'Refresh token already used')
            self.db.execute('DELETE FROM tokens WHERE family=?', (row['family'],))
            return self._issue(client.client_id, refresh_token.subject, scopes, refresh_token.resource)

    async def load_access_token(self, token):
        with self._lock:
            row = self.db.execute('SELECT t.data FROM tokens t JOIN clients c ON c.client_id=t.client_id '
                                  'WHERE t.token=? AND t.kind=? AND c.revoked=0', (token, 'access')).fetchone()
        if not row:
            return None
        access = AccessToken.model_validate_json(row['data'])
        if access.subject not in self.permissions.identities or access.resource != self.resource or access.expires_at <= time.time():
            return None
        return access

    async def revoke_token(self, token):
        with self._lock, self.db:
            self.db.execute('DELETE FROM tokens WHERE family IN (SELECT family FROM tokens WHERE token=?)', (token.token,))

    def clients_list(self):
        with self._lock:
            return [dict(row) for row in self.db.execute('SELECT client_id,identity,last_issued_at,revoked FROM clients ORDER BY client_id')]

    def revoke_client(self, client_id):
        with self._lock, self.db:
            if self.db.execute('UPDATE clients SET revoked=1 WHERE client_id=?', (client_id,)).rowcount != 1:
                raise ValueError('Unknown OAuth client')
            self.db.execute('DELETE FROM tokens WHERE client_id=?', (client_id,))
            self.db.execute('DELETE FROM pending WHERE client_id=?', (client_id,))
            # Codes are JSON records; remove all this client's outstanding grants.
            self.db.execute("DELETE FROM codes WHERE json_extract(data,'$.client_id')=?", (client_id,))


class CombinedTokenVerifier:
    def __init__(self, config, provider, permissions):
        self.tokens = load_tokens(config.env_path)
        self.provider = provider
        self.permissions = permissions

    async def verify_token(self, token):
        identity = self.tokens.get(token)
        if identity is not None:
            if identity not in self.permissions.identities:
                return None
            return AccessToken(token=token, client_id=identity, subject=identity,
                               scopes=self.provider.config.oauth_scopes, resource=self.provider.resource)
        return await self.provider.load_access_token(token)
