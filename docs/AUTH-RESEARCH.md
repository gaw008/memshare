# MCP authorization and client compatibility research

Research date: **2026-10-13**. This file records the official specification, official product documentation, and the source of the locally installed `mcp==2.3.0`. Client UIs and plan restrictions may change. Documentation research and local protocol tests do not replace verifying real connections from each vendor's account after deployment.

## Specification version and protocol choice

At the time of research, [MCP latest Authorization](https://modelcontextprotocol.io/specification/latest/basic/authorization) redirected to **2026-07-28**. HTTP authorization uses the OAuth authorization code flow with **PKCE S256**; authorization and token requests carry `resource`, and tokens must be bound to and validated against the target MCP resource. Invalid or expired tokens get 401; bearer tokens go in the request header, never in the URL. Whether refresh tokens are issued is up to the authorization server. [Authorization 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)

The resource server publishes **RFC 9728 Protected Resource Metadata**, whose `authorization_servers` points to the authorization server; the authorization server publishes **RFC 8414** metadata. A 401's `WWW-Authenticate` provides the `resource_metadata` URL. With the resource at `/mcp`, the standard discovery path is `/.well-known/oauth-protected-resource/mcp`, and the spec also allows the root path. memhub's public resource identifier is `https://mem.example.com/mcp`, and its issuer is the configured `public_url`, never derived from the reverse proxy's internal address. [Authorization Server Discovery](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/authorization-server-discovery)

**CIMD** uses an HTTPS JSON document URL as the `client_id`, which the authorization server must fetch, validate and cache. The latest spec recommends supporting CIMD; **RFC 7591 DCR is marked deprecated but kept as a compatibility mechanism**. Client registration priority is: existing pre-registration, CIMD, DCR, manual entry. Only declare `client_id_metadata_document_supported=true` when CIMD is actually supported. [Client Registration](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/client-registration)

This project uses **DCR + PKCE S256 + opaque access/refresh tokens** and does not claim CIMD. The SDK's authorization server supports DCR, Claude and ChatGPT let you explicitly choose DCR, and Gemini's official guide supports DCR; fetching CIMD documents online would conflict with the rule that the service depends on no external services at runtime. DCR is the compatibility choice and does not follow the latest spec's CIMD SHOULD recommendation; re-evaluate when upgrading.

`offline_access` is the authorization server's ability to issue refresh tokens, not an MCP resource permission: it may appear in the authorization server metadata, but not among the required scopes in the resource metadata or the 401 challenge. Actual file permissions are controlled separately by `clients.yaml`.

## What Python SDK 2.3.0 provides, and its limits

The version is pinned to [PyPI mcp 2.3.0](https://pypi.org/project/mcp/2.3.0/) (released 2026-10-02). The conclusions below were checked against both that version's official source and the files installed in the local `.venv`.

| SDK interface | Provided | memhub implements |
| --- | --- | --- |
| `OAuthAuthorizationServerProvider` | Interfaces for client lookup/registration, authorization redirect, code loading/exchange, access/refresh loading, refresh and revocation | SQLite persistence, expiry, one-time codes, refresh rotation, identity binding and local password login |
| `AuthSettings` | issuer, resource server URL, required scopes, registration/revocation options, `validate_token_resource` | Explicit public URL and resource validation; static tokens bound to the same resource |
| `ClientRegistrationOptions` | Registration switch, scope list, default scopes, client secret lifetime | DCR enabled; permissions never inferred from the registered name |
| `RevocationOptions` | Revocation endpoint switch | Endpoint enabled with persistent revocation |

References: [provider.py](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/auth/provider.py), [settings.py](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/auth/settings.py).

`create_auth_routes` provides:

| Route | Methods | Condition |
| --- | --- | --- |
| `/.well-known/oauth-authorization-server` | GET / OPTIONS | Always |
| `/authorize` | GET / POST | Always |
| `/token` | POST / OPTIONS | Always |
| `/register` | POST / OPTIONS | DCR enabled |
| `/revoke` | POST / OPTIONS | Revocation enabled |

`create_protected_resource_routes` generates the RFC 9728 routes for the resource path. SDK handlers do the protocol validation — registration requests, redirect URIs, scopes, client authentication, code lifetime, the PKCE verifier — while the provider owns business state and the token lifecycle. In SDK 2.3.0 the revocation request model makes a nullable client_secret mandatory, and Basic authentication additionally requires client_id in the body; memhub fills these gaps with a small form adapter and still leaves credential and token checks to the SDK. When an authorization request omits scope, the scope saved at DCR registration is used.

The bundled metadata builder only advertises `client_secret_post/basic` by default, even though the client authenticator also supports `none`, so memhub makes the metadata accurately include the public-client method `none`. The server routes never fetch CIMD documents: shared models having CIMD fields does not mean the built-in authorization server supports CIMD. [routes.py](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/auth/routes.py), [client_auth.py](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/auth/middleware/client_auth.py), [token.py](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/auth/handlers/token.py)

## Official requirements of the four chat apps

| Client | Registration, and this project's choice | Callback | Refresh token | Transport |
| --- | --- | --- | --- | --- |
| Claude custom connector | Supports both DCR and CIMD; choose **Register automatically** (DCR). CIMD would need the metadata to advertise both `true` and `none` | Officially `https://claude.ai/api/mcp/auth_callback`; DCR stores the exact URI from registration | Officially refreshes proactively and after a 401; public-client refresh should rotate. Claude requests `offline_access` when the AS advertises it | Streamable HTTP recommended; legacy SSE still supported |
| ChatGPT custom MCP app / plugin | CIMD recommended, DCR still supported; choose **DCR** explicitly | Currently may be `https://chatgpt.com/connector/oauth/{callback_id}`; the stable `https://chatgpt.com/connector_platform_oauth_redirect` is used only when RFC 9207 issuer identification is supported and advertised, and existing connections may keep the stable URI. Trust the admin page and the actual DCR value | Officially, check that refresh tokens are issued and that discovery advertises offline/refresh capability, to avoid re-login on expiry | Both SSE and streaming HTTP; use `/mcp` |
| Gemini personal custom app | The official guide confirms DCR auto-registration; without DCR you enter existing credentials under Advanced features. This project uses DCR; no official statement of CIMD support was found | The consumer help page found does not publish a fixed callback; it comes from the DCR request and is validated exactly | The help page does not publish a refresh requirement; this server still issues and rotates refresh tokens | The consumer docs require standard MCP; separate Enterprise Business docs say StreamableHTTP only, which cannot be taken as proof for the consumer app |
| Grok custom connector | Officially confirms the custom URL and required authentication, without publishing a DCR/CIMD choice. DCR is offered; still to be verified with a real account | The official docs found do not publish a fixed callback; nothing is hard-coded from third-party guesses | The official docs do not publish a refresh requirement; this server still issues and rotates refresh tokens | The official tunneling guide confirms Streamable HTTP works |

Claude sources: [Authentication for connectors](https://claude.com/docs/connectors/building/authentication), [Build an MCP server](https://claude.com/docs/connectors/building). ChatGPT sources: [Authentication](https://developers.openai.com/plugins/build/auth), [Add custom MCP server](https://developers.openai.com/api/docs/guides/custom-mcp-server), [Developer mode and MCP apps](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt). Gemini sources: [Connect & manage custom apps](https://support.google.com/gemini/answer/17209137?hl=en), [Enterprise Business MCP setup](https://support.google.com/g/answer/17106276?hl=en). Grok sources: [Connectors](https://docs.x.ai/grok/connectors), [Custom MCP Server Tunneling](https://docs.x.ai/grok/connectors/custom-mcp-tunneling).

**Identity boundary**: once the app is chosen on the login page, the authorization is bound to that memhub identity. DCR's `client_name` is self-reported by the client; it cannot replace password verification and explicit identity binding, and cannot prove a request came from a given vendor. The source suffix records the identity chosen for that authorization.

## Cloudflare Tunnel and Host validation

The SDK's `TransportSecuritySettings` controls DNS rebinding protection. When enabled, the Host must match `allowed_hosts` (an exact host or a `:*` port pattern), otherwise 421 is returned. An Origin, if present, must match `allowed_origins`; a missing Origin is allowed. Proxying to loopback does not automatically allow the public Host, so the production config must explicitly allow **`mem.example.com`** while keeping the loopback hosts for local health and manual checks. Do not fix failing Tunnel requests by disabling the check. [transport_security.py](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/transport_security.py)

`X-Forwarded-Proto: https` arrives through the loopback proxy. All public OAuth metadata and login URLs use `public_url`, and redirects use the client redirect URI validated by the SDK, so no external URL like `http://127.0.0.1:8796` is ever generated. Real Cloudflare HTTPS, reboot recovery, GitHub pushes and the four app connections must be verified by the operator after deployment.
