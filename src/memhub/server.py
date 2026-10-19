"""Seven MCP tools and an authenticated, stateless Streamable HTTP app."""

from collections.abc import AsyncIterator
from collections import defaultdict, deque
from datetime import datetime, timezone
import json
import base64
import binascii
from urllib.parse import unquote
import time
from functools import wraps
from contextlib import asynccontextmanager

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.auth.middleware.auth_context import AuthContextMiddleware, get_access_token
from mcp.server.auth.routes import create_auth_routes, build_metadata, cors_middleware, create_protected_resource_routes
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.auth.handlers.token import TokenHandler
from mcp.server.auth.handlers.revoke import RevocationHandler
from mcp.server.auth.middleware.client_auth import ClientAuthenticator
from mcp.server.transport_security import TransportSecuritySettings, RequestBodyLimitMiddleware, DEFAULT_MAX_REQUEST_BODY_SIZE
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.datastructures import FormData
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route, request_response

from memhub.auth import OAuthProvider, CombinedTokenVerifier
from memhub.permissions import PermissionPolicy
from memhub.config import Config
from memhub.storage import MemoryStore


def storage_errors(function):
    """Expose deliberate storage/permission refusals as SDK tool errors."""
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (ValueError, PermissionError) as error:
            raise ToolError(str(error)) from error
    return call


def create_app(config: Config) -> Starlette:
    permissions = PermissionPolicy(config.clients_path)
    provider = OAuthProvider(config, permissions)
    verifier = CombinedTokenVerifier(config, provider, permissions)
    resource_scopes = [s for s in config.oauth_scopes if s != "offline_access"]
    registration = ClientRegistrationOptions(enabled=True, valid_scopes=config.oauth_scopes, default_scopes=config.oauth_scopes)
    revocation = RevocationOptions(enabled=True)
    settings = AuthSettings(issuer_url=config.public_url, resource_server_url=provider.resource,
                            required_scopes=resource_scopes, client_registration_options=registration,
                            revocation_options=revocation, validate_token_resource=True)
    store = MemoryStore(config)
    mcp = MCPServer("memhub", instructions="每次新对话先调用 get_context", auth=settings, token_verifier=verifier)

    def identity() -> str:
        token = get_access_token()
        if token is None:
            raise ValueError("Authenticated identity is required")
        permissions.require_identity(token.subject)
        return token.subject

    def audit(tool: str, paths: list[str]) -> str:
        caller = identity()
        config.access_log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {"time": datetime.now(timezone.utc).isoformat(), "identity": caller, "tool": tool, "paths": paths}
        with config.access_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        config.access_log_path.chmod(0o600)
        return caller

    @mcp.tool()
    @storage_errors
    def get_context() -> dict:
        """Call this at the start of EVERY new conversation before answering.
        Returns profile and preferences facts plus the permitted file catalog (path -> description).
        Source suffixes and versions are omitted; use read for full content before update.
        Do NOT call repeatedly for topic detail; use search and read for those.
        """
        paths = []
        try:
            result = store.get_context(identity=identity())
            paths = list(result["files"])
            # Clients reliably read tool results, unlike initialize instructions.
            return {**result, "instructions": "每个话题结束时调用 log，用一行记下用户问了什么、卡在哪里或做了什么决定；不记你的回答和闲聊。"}
        finally:
            audit("get_context", paths)

    @mcp.tool()
    @storage_errors
    def search(query: str, limit: int = config.search_default_limit) -> dict:
        """Call when a question needs the user's stored personal facts or topic aliases.
        Do NOT use to infer new facts or when full file content is needed; use read.
        """
        paths = []
        try:
            result = store.search(query, limit, identity=identity())
            paths = sorted({hit["path"] for hit in result["hits"]})
            return result
        finally:
            audit("search", paths)

    @mcp.tool(description=(
        "Call for full Markdown content and a version before updating a fact. "
        f"Pass at most {config.read_max_paths} paths per call; split larger sets into several calls. "
        "Do NOT use to enumerate files; get_context supplies the catalog."))
    @storage_errors
    def read(paths: list[str]) -> dict:
        return store.read(paths, identity=audit("read", paths))

    @mcp.tool()
    @storage_errors
    def remember(path: str, fact: str, description: str | None = None, confirm: bool = False) -> dict:
        """Call only to store enduring facts explicitly stated by the user.
        Do NOT store AI guesses, recommendations, or one-off chat. When facts change,
        use update to replace the original; optionally retain '(之前：...)' for history.
        Ask the user BEFORE writing profile.md and pass confirm=true after consent.
        New files require a description. Do NOT supply a source/date suffix: the server
        appends authenticated identity and date. Duplicate candidates require update.
        """
        return store.remember(path, fact, audit("remember", [path]), description, confirm)

    @mcp.tool()
    @storage_errors
    def update(path: str, old_text: str, new_text: str, version: str) -> dict:
        """Call when a user explicitly changes an enduring fact, after read supplies version.
        Replace the old fact; optionally keep '(之前：...)'. Do NOT append changed facts
        with remember, store guesses, AI advice or one-off chat, or add a source suffix.
        Ask the user BEFORE changing profile.md. On version conflict, read the returned
        latest content and resolve the change before calling again.
        """
        return store.update(path, old_text, new_text, version, audit("update", [path]))

    @mcp.tool()
    @storage_errors
    def forget(path: str, text: str) -> dict:
        """Call when the user asks to remove one stored fact, matching exactly one fact line.
        Do NOT use to replace a changed fact (use update) or to delete an entire file.
        Ask the user BEFORE changing profile.md.
        """
        return store.forget(path, text, audit("forget", [path]))

    @mcp.tool()
    @storage_errors
    def log(entry: str) -> dict:
        """Call once when a conversation topic wraps up, to append it to today's shared journal.
        Write one line, in the user's language: what the user asked, and where they got stuck
        or what they decided. Do NOT log every message, your own answer, or trivial chat.
        Do NOT add a time or source; the server appends them and picks today's file.
        """
        paths = []
        try:
            result = store.log(entry, identity())
            paths = [result["path"]]
            return result
        finally:
            audit("log", paths)

    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/mcp", stateless_http=True, json_response=True,
        host=config.host,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                                     allowed_hosts=config.allowed_hosts, allowed_origins=config.allowed_origins),
    )

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            store.close()
            provider.close()

    async def healthz(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    routes = create_auth_routes(provider, settings.issuer_url,
                                client_registration_options=registration, revocation_options=revocation)
    # SDK implements public-client authentication but omits "none" in its metadata.
    metadata = build_metadata(settings.issuer_url, None, registration, revocation)
    metadata.token_endpoint_auth_methods_supported.append("none")
    metadata.revocation_endpoint_auth_methods_supported.append("none")

    async def authorization_metadata(request: Request):
        return JSONResponse(metadata.model_dump(mode="json", exclude_none=True))

    token_handler = TokenHandler(provider, ClientAuthenticator(provider))
    revoke_handler = RevocationHandler(provider, ClientAuthenticator(provider))

    async def token_exchange(request: Request):
        # SDK validates PKCE, redirects, client credentials and scopes. It does not
        # check the resource indicator; an omitted one means this MCP endpoint.
        form = await request.form()
        if "resource" in form and form["resource"] != provider.resource:
            return JSONResponse({"error": "invalid_target", "error_description": "resource must identify this MCP endpoint"},
                                status_code=400, headers={"Cache-Control": "no-store"})
        return await token_handler.handle(SDKFormRequest(request))

    async def revoke(request: Request):
        return await revoke_handler.handle(SDKFormRequest(request, optional_secret=True))

    for route in routes:
        if route.path == "/.well-known/oauth-authorization-server":
            route.app = cors_middleware(authorization_metadata, ["GET", "OPTIONS"])
        elif route.path in {"/token", "/revoke"}:
            route.app = CORSMiddleware(
                RequestBodyLimitMiddleware(request_response(token_exchange if route.path == "/token" else revoke), DEFAULT_MAX_REQUEST_BODY_SIZE),
                allow_origins="*", allow_methods=["POST", "OPTIONS"], allow_headers=["MCP-Protocol-Version"],
            )
    # RFC 9728 publishes metadata under the resource path; some clients probe the root first.
    resource_metadata = create_protected_resource_routes(settings.resource_server_url, [settings.issuer_url],
                                                         scopes_supported=settings.required_scopes)[0]
    routes.append(Route("/.well-known/oauth-protected-resource", resource_metadata.endpoint, methods=["GET", "OPTIONS"]))
    routes.extend([Route("/healthz", healthz), Route("/oauth/login", provider.login, methods=["GET", "POST"]),
                   Mount("/", app=mcp_app)])
    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.oauth_provider = provider
    app.state.store = store
    app.add_middleware(TokenRateLimit, verifier=verifier, limit=config.rate_limit_per_minute)
    return app


class TokenRateLimit:
    """A rolling minute of authenticated HTTP requests to /mcp, keyed by token."""
    def __init__(self, app, verifier, limit):
        self.app = app
        self.verifier = verifier
        self.limit = limit
        self.requests = defaultdict(deque)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] == "/mcp":
            request = Request(scope)
            header = request.headers.get("authorization", "")
            if header.lower().startswith("bearer "):
                token = header[7:]
                if await self.verifier.verify_token(token):
                    now = time.monotonic()
                    # Expire idle buckets as well as individual timestamps.
                    for key in list(self.requests):
                        if not self.requests[key] or self.requests[key][-1] <= now - 60:
                            del self.requests[key]
                    history = self.requests[token]
                    while history and history[0] <= now - 60:
                        history.popleft()
                    if len(history) >= self.limit:
                        await JSONResponse({"error": "rate_limit_exceeded"}, status_code=429,
                                           headers={"Retry-After": str(max(1, int(60 - (now - history[0]))))})(scope, receive, send)
                        return
                    history.append(now)
        await self.app(scope, receive, send)


class SDKFormRequest(Request):
    """Normalize SDK 2.3.0 form omissions without replacing its credential checks."""
    def __init__(self, request: Request, optional_secret: bool = False):
        super().__init__(request.scope, request.receive)
        self.original = request
        self.optional_secret = optional_secret

    async def form(self, *args, **kwargs):
        fields = dict(await self.original.form(*args, **kwargs))
        header = self.headers.get("authorization", "")
        if "client_id" not in fields and header.startswith("Basic "):
            try:
                credentials = base64.b64decode(header[6:], validate=True).decode("utf-8")
                client_id, _ = credentials.split(":", 1)
            except (ValueError, UnicodeDecodeError, binascii.Error):
                # Leave invalid credentials to the SDK's authentication error response.
                pass
            else:
                fields["client_id"] = unquote(client_id)
        if self.optional_secret and "client_secret" not in fields:
            fields["client_secret"] = None
        return FormData(fields)
