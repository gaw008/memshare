# memshare

[English](README.md) | **简体中文**

**让你所有的 AI 共用同一份关于你的记忆。**

> 仓库叫 memshare，服务、命令行和 MCP 连接名叫 `memhub`。

如果你像很多人一样，按专长分工使用 AI——Claude Code 写代码、Gemini 做日常问答、ChatGPT 查资料、Grok 写文案——你会发现它们彼此不认识：在 A 那里说过的目标、习惯、项目进展，B 一无所知，每次都要重讲一遍。

memhub 是一个自托管的远程 MCP 服务，把"关于你的长期事实"存成一份 Markdown，任何支持 MCP 的 AI 都能读写。它像一个所有助理共用的**档案柜**：谁记了什么、什么时候记的，每一行都写得清清楚楚。

```
Claude / ChatGPT / Gemini / Grok（OAuth）  Claude Code / Gemini CLI / Codex（静态 token）
                    \                                   /
                     └──── HTTPS /mcp（Streamable HTTP）─┘
                                     │
                              memhub（本项目）
                     鉴权 → 按身份查权限 → 7 个 MCP 工具
                                     │
               Markdown 文件（唯一事实来源，每次写入一个 git commit）
               SQLite FTS5 索引（可随时从文件重建，支持中文分词）
```

## 特点

- **Markdown 是唯一事实来源**：一行一个事实，人能直接读、直接改；每次写入自动 `git commit`，可定时推到你自己的私有 GitHub 仓库备份。
- **来源可追溯**：每行末尾的 `[gemini · 2026-10-13]` 由服务端按鉴权身份追加，模型无法伪造。
- **为模型设计的少量工具**：工具越少，各家模型越能用对。工具描述就是给模型的提示词，写清"什么时候调用、什么时候不调用"。
- **服务端兜底的规则**：写入前查重（相近就返回候选，让模型改用 update）；写 `profile.md` 需要显式确认；单文件 16 KB 上限；用正则拦截证件号、银行卡号、密码、API key。
- **按身份授权**：聊天 app 用 OAuth 2.1（DCR + PKCE），CLI 用静态 bearer token；每个身份能读写哪些路径，在 `clients.yaml` 里用 glob 配置。
- **每日对话日志**：各个 AI 把每个话题的提问和卡点追加到同一份当天日志，方便第二天让 AI 帮你复盘。
- **运行时不依赖外部服务**：不调用任何 LLM API，不用 CDN、远程字体或在线下载。

## 七个工具

| 工具 | 作用 |
| --- | --- |
| `get_context` | 新对话第一步调用。返回 profile、preferences 的事实行和"路径 → 描述"的文件目录，控制在约 2K tokens |
| `search` | 全文检索事实（同时匹配文件描述和别名），中文按词切分 |
| `read` | 读文件全文和 `version`（每次最多 10 个文件），改写前先读 |
| `remember` | 追加一条用户明确说过的长期事实；相近内容返回候选而不写入 |
| `update` | 用 `version` 做乐观并发，替换恰好一处的旧事实 |
| `forget` | 删除一行事实（不能删整个文件） |
| `log` | 把一个话题的"问了什么、卡在哪、做了什么决定"追加到 `journal/YYYY-MM-DD.md`；只追加不可改，不进目录和搜索 |

## 记忆长什么样

```
memory/
├── profile.md             身份与长期目标（写入需确认）
├── preferences.md         回答方式与长期偏好
├── _inbox.md              还没归类的事实
├── topics/fitness.md      主题：健身、投资、技术……
├── areas/project-x.md     项目
├── people/friend.md       关于别人的事实（主语写明是谁）
└── journal/2026-10-17.md  每日对话日志（只追加）
```

```markdown
---
name: fitness
description: 训练、体重、饮食习惯
aliases: [健身, workout]
updated: '2026-10-13'
---
- 每周跑步三次 [claude-code · 2026-10-13]
- 正在做增肌训练，目标体重 70kg [gemini · 2026-10-14]
```

## 本地快速开始

需要 Python 3.12、[uv](https://docs.astral.sh/uv/)、Git。所有命令都要显式指定参数文件；相对路径以参数文件所在目录为基准，缺项直接报错，没有隐式默认值。

```bash
git clone https://github.com/gaw008/memshare.git && cd memshare
uv sync --locked
# 生成两个 CLI 身份的随机 token（仅当还没有 .env 时）：
uv run python - <<'PY'
import json, secrets
from pathlib import Path
p = Path('.env')
with p.open('x', encoding='utf-8') as f:
    f.write("STATIC_TOKENS='" + json.dumps({secrets.token_urlsafe(48): app for app in ('claude-code', 'gemini-cli')}) + "'\n")
p.chmod(0o600)
PY
uv run memhub --config memhub.toml init           # 初始化独立的记忆 git 仓库（./dev-memory）
uv run memhub --config memhub.toml set-password   # 设置 OAuth 登录口令（argon2 哈希写入 .env）
uv run memhub --config memhub.toml serve          # 监听 127.0.0.1:8787
```

本地接入 Claude Code（token 从 `.env` 里取 `claude-code` 对应的那个）：

```bash
claude mcp add --transport http --scope user memhub \
  http://127.0.0.1:8787/mcp --header 'Authorization: Bearer <claude-code-token>'
```

然后在新会话里说"先调用 memhub 的 get_context"。

## 部署到公网，接入聊天 app

ChatGPT、Claude 网页/手机、Gemini、Grok 这类聊天 app 只能连接公网 HTTPS 地址。

1. **部署**：参考 [docs/DEPLOY.md](docs/DEPLOY.md)（英文）。参考方案是一台 Ubuntu 服务器 + systemd + Cloudflare Tunnel（不用开公网端口），附带安装、更新脚本和每小时备份 timer。先把 `memhub.prod.toml` 里的 `mem.example.com`、`YOUR_GITHUB_USER` 换成你自己的。
2. **接入各家 app**：参考 [docs/CLIENTS.md](docs/CLIENTS.md)（英文），包含 Claude、ChatGPT、Gemini、Grok、Claude Code、Gemini CLI 的步骤。聊天 app 授权时会跳到 memhub 的登录页，输入口令并选择"这是哪个 app"，这个选择决定该连接的身份和权限。
3. **给每家 AI 加一段自定义指令**（模板如下）。MCP 的 initialize instructions 不是每家都会遵守，写进自定义指令最可靠。

```text
我接入了个人记忆库 memhub（MCP 工具）。规则：
1. 每次新对话先调用 get_context，再回答。
2. 问题涉及我的个人情况（健康、项目、家人、偏好）时，先 search 再答。
3. 我说出长期有效的新事实（目标、习惯、决定、项目进展、关于某人的情况）时，调用 remember 记下；旧事实变了用 update。
4. 不记：一次性闲聊、你的推测、你给我的建议（除非我明确采纳）。
5. 写 profile 前先问我。
6. 每个话题结束时调用 log，用一行记下我问了什么、卡在哪里或做了什么决定。
```

> 给 memhub 新增工具后，部分聊天 app 会缓存旧的工具列表，需要在它的连接器设置里刷新或重新连接，并开一个新对话。

## 配置

| 文件 | 内容 |
| --- | --- |
| `memhub.toml` / `memhub.prod.toml` | 开发 / 生产参数：目录、端口、时区（决定日志按哪天分文件）、文件上限、上下文 token 预算、限流、允许的 Host、敏感信息正则等 |
| `.env` | 密钥：CLI 静态 token、OAuth 口令哈希。不进 git，参照 `.env.example` |
| `clients.yaml` | 每个身份可读写的路径 glob。默认所有身份读写全部 |

授权管理和批量导入：

```bash
uv run memhub --config memhub.toml clients list
uv run memhub --config memhub.toml clients revoke '<client_id>'
uv run memhub --config memhub.toml import --source gemini \
  --path topics/fitness.md --description '健身与训练' facts.txt
```

导入时每个非空行是一条事实，逐条走 remember 的权限、敏感拦截和查重；`profile.md` 不可导入。

## 安全与边界

- 访问日志只记录时间、身份、工具名和路径，不记录事实内容、查询词或 token。
- 按 token 限流（默认每分钟 60 次），超限返回 429。
- 敏感信息拦截只覆盖配置里的正则形态，不是万能的；不要把它当成存放机密的地方。
- 身份是用户登录时选择并绑定的 app 标签，不是对厂商客户端真实性的认证。
- 单进程写锁 + 原子替换文件；不处理多进程或外部同时编辑的事务冲突。手工改了 Markdown 后重启服务即可重建索引。
- OAuth 数据库和 `.env` 是凭据状态，不在 Markdown 备份里，需要自己备份服务器状态。
- 各家接入现状（截至 2026-10）：Claude、ChatGPT 支持选择 DCR；Gemini 个人版官方确认 DCR，但有地区、语言、账号类型限制；Grok 官方未公开完整的 OAuth 注册细节，兼容性需自行验证。详见 [docs/AUTH-RESEARCH.md](docs/AUTH-RESEARCH.md)（英文）。

## 开发

```bash
uv run pytest -q
# 依赖变更后导出给服务器用的 requirements.txt：
uv export --no-hashes --no-dev --no-emit-project --output-file requirements.txt
```

测试的临时文件和记忆仓库都在 `.test-tmp/`，不会碰真实记忆。技术栈：Python 3.12、官方 [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)（Streamable HTTP，路径 `/mcp`，健康检查 `/healthz`）、SQLite FTS5、jieba。

## License

[MIT](LICENSE)
