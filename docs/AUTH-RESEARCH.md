# MCP 授权与客户端兼容性查证

查证日期：**2026-10-13**。本文件记录官方规范、官方产品文档及本地安装的 `mcp==2.3.0` 源码；客户端 UI 和套餐限制可能变化。文档查证与本地协议测试不能替代部署后四家账号的实际接入验收。

## 规范版本与协议选择

查证时访问 [MCP latest Authorization](https://modelcontextprotocol.io/specification/latest/basic/authorization) 重定向到 **2026-07-28**。HTTP 授权使用 OAuth authorization code + **PKCE S256**；授权和 token 请求携带 `resource`，令牌须绑定并校验目标 MCP 资源。无效或过期令牌返回 401，Bearer 令牌放在请求头，不能放进 URL。Refresh token 是否签发由授权服务器决定。[Authorization 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)

资源服务器发布 **RFC 9728 Protected Resource Metadata**，其中 `authorization_servers` 指向授权服务器；授权服务器发布 **RFC 8414** 元数据。401 的 `WWW-Authenticate` 提供 `resource_metadata` URL。资源路径为 `/mcp` 时，标准发现路径为 `/.well-known/oauth-protected-resource/mcp`，规范也允许根路径。memhub 对外资源标识为 `https://mem.example.com/mcp`，issuer 为配置中的 `public_url`，不从反向代理的内部地址推导。[Authorization Server Discovery](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/authorization-server-discovery)

**CIMD** 使用 HTTPS JSON 文档 URL 作为 `client_id`，需要授权服务器获取、校验并缓存客户端元数据。最新规范建议支持 CIMD；**RFC 7591 DCR 已标为 deprecated，但仍保留为兼容机制**。客户端注册优先级为已有预注册信息、CIMD、DCR、人工输入。支持 CIMD 时才应声明 `client_id_metadata_document_supported=true`。[Client Registration](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/client-registration)

本项目选择 **DCR + PKCE S256 + 不透明 access/refresh token**，不声称实现 CIMD。原因是现有 SDK 授权服务器支持 DCR，Claude、ChatGPT 可显式选择 DCR，Gemini 官方接入指南支持 DCR；CIMD 在线拉取与项目运行时不依赖外部服务的约束冲突。DCR 是兼容选择，未实现最新规范的 CIMD SHOULD 建议，应在升级时重新评估。

`offline_access` 是授权服务器签发 refresh token 的能力，不是 MCP 资源访问权限：可以出现在授权服务器元数据，不应出现在资源元数据或 401 challenge 的必需 scope 中。实际文件权限独立由 `clients.yaml` 控制。

## Python SDK 2.3.0 的现成能力与边界

版本锁定为 [PyPI mcp 2.3.0](https://pypi.org/project/mcp/2.3.0/)（发布于 2026-10-02）。以下结论同时核对了该版本官方源码与本地 `.venv` 的安装文件。

| SDK 接口 | 已有能力 | memhub 需要实现 |
| --- | --- | --- |
| `OAuthAuthorizationServerProvider` | 客户端查找/注册、授权重定向、授权码加载/交换、access/refresh 加载、刷新和吊销的接口 | SQLite 持久化、过期、一用授权码、refresh rotation、身份绑定和本地口令登录 |
| `AuthSettings` | issuer、resource server URL、必需 scope、注册/吊销选项，`validate_token_resource` | 显式设置公网 URL 与资源校验，静态 token 也绑定同一资源 |
| `ClientRegistrationOptions` | 注册开关、scope 列表、默认 scope、client secret 有效期 | 开启 DCR；权限不从注册名称推断 |
| `RevocationOptions` | 吊销端点开关 | 开启端点并实现持久化吊销 |

参考：[provider.py](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/auth/provider.py)、[settings.py](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/auth/settings.py)。

`create_auth_routes` 自动提供：

| 路由 | 方法 | 条件 |
| --- | --- | --- |
| `/.well-known/oauth-authorization-server` | GET / OPTIONS | 始终 |
| `/authorize` | GET / POST | 始终 |
| `/token` | POST / OPTIONS | 始终 |
| `/register` | POST / OPTIONS | DCR 开启 |
| `/revoke` | POST / OPTIONS | 吊销开启 |

`create_protected_resource_routes` 按资源路径生成 RFC 9728 路由。SDK handler 承担注册请求、redirect URI、scope、客户端鉴权、授权码有效期、PKCE verifier 等协议校验；provider 负责业务状态与令牌生命周期。SDK 2.3.0 撤销请求模型将可空 client_secret 设为必填，Basic 鉴权又要求正文 client_id；memhub 用小型表单适配补齐缺项，凭据和 token 检查仍交给 SDK。授权请求省略 scope 时采用 DCR 保存的 scope。

现成 metadata builder 默认只广告 `client_secret_post/basic`，尽管 client authenticator 也支持 `none`，所以 memhub 需要让元数据准确包含 public-client `none`。服务端路由不会自动获取 CIMD 文档：共享模型有 CIMD 字段不等于内置授权服务器已支持 CIMD。[routes.py](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/auth/routes.py)、[client_auth.py](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/auth/middleware/client_auth.py)、[token.py](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/auth/handlers/token.py)

## 四家聊天 app 的官方要求

| 客户端 | 注册方式及本项目选择 | 回调地址 | Refresh token | 传输 |
| --- | --- | --- | --- | --- |
| Claude custom connector | DCR、CIMD 均支持；选 **Register automatically**（DCR）。CIMD 需要 metadata 同时广告 true 和 `none` | 官方指定 `https://claude.ai/api/mcp/auth_callback`；DCR 保存注册时的精确 URI | 官方描述主动及 401 后刷新；public-client refresh 应轮换。AS 广告 `offline_access` 时 Claude 会请求它 | 官方建议 Streamable HTTP，仍支持旧 SSE |
| ChatGPT custom MCP app / plugin | CIMD 推荐，DCR 仍受支持；明确选 **DCR** | 当前可能为 `https://chatgpt.com/connector/oauth/{callback_id}`；支持且广告 RFC 9207 issuer identification 才使用稳定地址 `https://chatgpt.com/connector_platform_oauth_redirect`，既有连接亦可能沿用稳定地址。以管理页和 DCR 实际值为准 | 官方要求检查 refresh 签发和 discovery 的 offline/refresh 能力，避免到期后重新登录 | SSE 与 streaming HTTP 均支持；使用 `/mcp` |
| Gemini personal custom app | 官方指南确认 DCR 自动注册；无 DCR 时在 Advanced features 输入已有凭据。本项目使用 DCR；未找到官方承诺 CIMD 的资料 | 本次找到的个人 Gemini 官方帮助页未公开固定 callback；由 DCR 请求提供并精确校验 | 该帮助页未公开 refresh 强制要求；本服务仍签发并轮换 refresh | 个人版文档要求标准 MCP；单独的 Enterprise Business 官方文档明确仅 StreamableHTTP，不能将企业要求直接当作个人版证明 |
| Grok custom connector | 官方确认 Custom URL 与所需认证；未公开其 DCR/CIMD 具体选择。提供 DCR 协议能力，仍待真实账号验收 | 本次官方文档未公开固定 callback；不据第三方猜测硬编码 | 官方文档未公开 refresh 强制要求；本服务仍签发并轮换 refresh | 官方 tunneling 指南确认 Streamable HTTP 可用 |

Claude 依据：[Authentication for connectors](https://claude.com/docs/connectors/building/authentication)、[Build an MCP server](https://claude.com/docs/connectors/building)。ChatGPT 依据：[Authentication](https://developers.openai.com/plugins/build/auth)、[Add custom MCP server](https://developers.openai.com/api/docs/guides/custom-mcp-server)、[Developer mode and MCP apps](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt)。Gemini 依据：[Connect & manage custom apps](https://support.google.com/gemini/answer/17209137?hl=en)、[Enterprise Business MCP setup](https://support.google.com/g/answer/17106276?hl=en)。Grok 依据：[Connectors](https://docs.x.ai/grok/connectors)、[Custom MCP Server Tunneling](https://docs.x.ai/grok/connectors/custom-mcp-tunneling)。

**身份边界**：登录页选定 app 后将授权绑定到 memhub 身份；DCR 的 `client_name` 是客户端自报名称，不能代替口令验证和显式身份绑定，也不能据此证明请求来自某厂商。项目的来源后缀代表这次授权选定的身份。

## Cloudflare Tunnel 与 Host 校验

SDK 的 `TransportSecuritySettings` 控制 DNS rebinding 校验。启用后，Host 必须命中 `allowed_hosts`（精确主机或 `:*` 端口模式）；不匹配返回 421。Origin 若存在必须命中 `allowed_origins`，缺失允许通过。反代到 loopback 不会自动允许公网 Host，因此生产文件需要明确允许 **`mem.example.com`**，并保留本地健康/手工检查用的 loopback host。不要靠关闭校验解决 Tunnel 请求失败。[transport_security.py](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/transport_security.py)

`X-Forwarded-Proto: https` 由 loopback 代理接入；所有对外 OAuth 元数据和登录 URL 使用 `public_url`，回跳使用 SDK 校验过的客户端 redirect URI，避免生成 `http://127.0.0.1:8796` 的外部 URL。真实 Cloudflare HTTPS、重启恢复、GitHub push 和四家 app 接入均需部署后由用户验收。
