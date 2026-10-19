# memhub 客户端接入

官方入口查证：**2026-10-13**，各家界面可能已变化，以实际页面为准。下文用 **`https://mem.example.com/mcp`** 代表你的公网 MCP 地址，替换成自己的域名。协议与 SDK 查证详见 [AUTH-RESEARCH.md](AUTH-RESEARCH.md)。

## 共同准备

1. 按 [README](../README.md) 部署服务与独立 Tunnel，确认公网元数据可访问，未授权 `/mcp` 返回 401 且有 `WWW-Authenticate`。
2. 在服务器设置一次授权口令：

   ```bash
   sudo -u memhub /srv/memhub/app/.venv/bin/memhub \
     --config /srv/memhub/app/memhub.prod.toml set-password
   ```

3. 聊天 app 使用 **OAuth authorization code + PKCE S256 + DCR**。登录页面输入口令，在“这是哪个 app”里选择当前 app，授权回跳后再在新对话调用 `get_context`。CLI 使用各自静态 token。
4. 服务按登录时选择的身份应用 `clients.yaml` 权限；不能把聊天 app 当成 CLI 身份使用。DCR 注册名称不决定权限。

权限：默认 `clients.yaml` 让所有身份读写全部路径，可按需收紧（例如只让写文案的 AI 读 `topics/social-media.md`）。

## Claude（网页、Desktop、mobile、Cowork）

1. 在个人账户打开 **Customize → Connectors → + Add → Add custom connector**。Team/Enterprise 由有相应权限的 Owner 在 Organization settings → Connectors 添加，再由成员 Connect。
2. 名称填 `memhub`，远程 URL 填 `https://mem.example.com/mcp`。
3. Authentication 选 **Sign in now**；OAuth client 选 **Register automatically**。本服务使用 DCR，因此不要选择依赖 CIMD 的 published identity 模式。
4. 完成口令页并选择 `claude`；在新对话的 `+ → Connectors` 中启用 memhub。

Hosted callback 是 `https://claude.ai/api/mcp/auth_callback`，服务通过 DCR 获取并校验；无需手动在代码里新增回调。Claude 支持 DCR、PKCE S256、Streamable HTTP 和 refresh token；本服务提供 refresh rotation。官方入口：[Custom connectors](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)；技术要求：[Authentication](https://claude.com/docs/connectors/building/authentication)、[Transport](https://claude.com/docs/connectors/building)。

## ChatGPT（custom MCP plugin / developer mode app）

官方文档当前有两类入口，以账号显示的入口为准：

1. **新的 plugin 入口**：打开 ChatGPT Plugins，点击 `+ → Add custom MCP server`，输入名称和 Server URL，认证选 OAuth，客户端注册方式明确选 **DCR**；按页面提示创建为 plugin，再安装并在对话中 `@memhub`。
2. **developer mode app 入口**：符合工作区权限的用户在 **Settings → Apps → Advanced Settings** 启用 developer mode；从 Apps → Create 输入地址和 OAuth 设置，Scan Tools，完成登录（身份选 `chatgpt`），再 Create。Business / Enterprise / Edu 管理员也可从 Workspace settings → Apps → Create 操作。

回调可能为 `https://chatgpt.com/connector/oauth/{callback_id}`，也可能是已有连接的稳定 callback。使用注册请求里的精确 URI，不手动复用别的连接的 callback。CIMD 是 OpenAI 推荐方式，但仍支持配置 DCR；此项目不广告 CIMD。持续连接依赖 refresh 签发及 AS 元数据中的 `offline_access` 能力。官方：[Add custom MCP server](https://developers.openai.com/api/docs/guides/custom-mcp-server)、[OAuth / callback](https://developers.openai.com/plugins/build/auth)、[Developer mode / refresh / plan availability](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt)。

官方 developer mode 帮助页说明 full MCP 写入权限面向 Business / Enterprise / Edu；Pro 的该入口可能仅 read/fetch。即使服务允许写入，ChatGPT 套餐和工作区也可能不开放写工具。使用新 plugin 入口时按页面实际权限验收。

## Gemini 个人网页与手机 app

1. 在 **gemini.google.com → Settings → Connected Apps**；如果没有该入口，先进入 Personal Intelligence → Connected Apps。
2. 在 Custom apps 点击 **Add a custom app**，输入 MCP URL，Next。因为服务提供 DCR，无需填写 Advanced features 里的手工 client credentials。
3. 完成登录（身份选 `gemini`），回到 Gemini 后，在新对话输入 `@` 选择 memhub。
4. 官方说明只能在网页添加，添加后网页和 mobile 均可使用。该功能当前要求美国地区、18 岁以上、个人 Google Account、Keep Activity 开启，且仅英语界面可用。

依据：[Gemini Apps 官方帮助](https://support.google.com/gemini/answer/17209137?hl=en)。此个人版帮助页未公开固定 callback、CIMD 支持或 refresh token 强制要求；服务提供标准 DCR、PKCE 和 refresh，callback 由注册请求决定。**不要**使用 Gemini Enterprise 的管理入口代替个人 Connected Apps；Enterprise Business 另有仅支持 StreamableHTTP 的要求和不同配置步骤：[企业官方指南](https://support.google.com/g/answer/17106276?hl=en)。

## Grok

1. 在 **grok.com/connectors → New Connector → Custom** 输入 MCP URL。
2. 按页面完成认证；若页面提供注册方式，选 DCR / 自动注册。口令页身份选 `grok`。
3. 在新对话使用 memhub 的 `get_context`，确认能看到全部文件。

官方确认 custom connector 入口、公网可达要求，以及 Streamable HTTP 可经 Tunnel 连接：[Connectors](https://docs.x.ai/grok/connectors)、[Tunneling](https://docs.x.ai/grok/connectors/custom-mcp-tunneling)。当前官方文档没有公开 DCR/CIMD、固定 callback 或 refresh 强制要求；本服务提供 DCR 能力，**Grok 真实授权兼容性仍待部署后确认**。如果当前账号只提供预注册客户端方式，记录页面所需字段和 callback，再评估，不能以本地协议测试宣称已接入。

## Claude Code

从安装生成的 `/srv/memhub/state/.env` 私下取得 `claude-code` 静态 token，替换占位符：

```bash
claude mcp add --transport http --scope user memhub \
  https://mem.example.com/mcp \
  --header 'Authorization: Bearer <claude-code-token>'
claude mcp list
```

把下方指令模板加入全局 `CLAUDE.md`。启动新会话，用 `/mcp` 查看工具，再调用 `get_context`。静态 token 身份为 `claude-code`，不用聊天 OAuth 下拉选择。官方：[Claude Code MCP](https://code.claude.com/docs/en/mcp)。

## Gemini CLI

取得 `gemini-cli` 静态 token 后，使用官方 HTTP transport 与 header 参数：

```bash
gemini mcp add --transport http --scope user \
  --header 'Authorization: Bearer <gemini-cli-token>' \
  memhub https://mem.example.com/mcp
gemini mcp list
```

也可以在自己的 `~/.gemini/settings.json` 合并以下片段（保留已有其他配置）：

```json
{
  "mcpServers": {
    "memhub": {
      "httpUrl": "https://mem.example.com/mcp",
      "headers": {"Authorization": "Bearer <gemini-cli-token>"}
    }
  }
}
```

指令模板放全局 `GEMINI.md`；新会话 `/mcp list` 验证。官方：[Gemini CLI MCP](https://geminicli.com/docs/tools/mcp-server/)。

## 每家自定义指令模板

```text
我接入了个人记忆库 memhub（MCP 工具）。规则：
1. 每次新对话先调用 get_context，再回答。
2. 问题涉及我的个人情况（健身、项目、家人、偏好）时，先 search 再答。
3. 我说出长期有效的新事实（目标、习惯、决定、项目进展、关于某人的情况）时，调用 remember 记下；旧事实变了用 update。
4. 不记：一次性闲聊、你的推测、你给我的建议（除非我明确采纳）。
5. 写 profile 前先问我。
6. 每个话题结束时调用 log，用一行记下我问了什么、卡在哪里或做了什么决定。
```

模板不能改变服务端权限，权限只由 `clients.yaml` 决定。工具描述和 MCP initialize instructions 能提示模型，但不能保证每家产品一定自动调用；首次验证时明确要求“先用 memhub 的 get_context”。

## 授权管理与验收

```bash
sudo -u memhub /srv/memhub/app/.venv/bin/memhub \
  --config /srv/memhub/app/memhub.prod.toml clients list
sudo -u memhub /srv/memhub/app/.venv/bin/memhub \
  --config /srv/memhub/app/memhub.prod.toml clients revoke '<client_id>'
```

每个 app 验证：授权完成、`get_context` 成功、可写 app 的新事实来源与所选身份一致、access 过期后能刷新、revoke 后拒绝访问。跨 app 验证：Claude Code 记下一条用户真实确认的健身事实，然后 Gemini 搜到同一事实；每家写入的来源后缀与其授权身份一致。
