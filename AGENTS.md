# memhub · 开发约定

跨 AI 共享记忆的远程 MCP 服务。给在本仓库工作的人和 AI 编码助手看；用户文档见 `README.md`。

## 要点

- 记忆是独立 git 仓库里的 Markdown 文件，唯一事实来源；SQLite FTS5 索引可随时从文件重建（启动全量建，写入后增量更新）。
- Python 3.12 + 官方 MCP Python SDK，Streamable HTTP，路径 `/mcp`；`/healthz` 健康检查。
- 7 个工具：`get_context` `search` `read` `remember` `update` `forget` `log`。`log` 只追加到 `journal/YYYY-MM-DD.md`（按 `timezone` 取当天），行尾 `[身份 · HH:MM]`；日志不进 `get_context` 目录和搜索索引，不能被 remember/update/forget 改写，可用 `read` 读取。`get_context` 返回紧凑格式（"路径 → 描述"目录 + 不带来源后缀的事实行），并带 `instructions` 字段提醒模型话题结束时调用 `log`。
- 工具描述用英文写，写清何时调用、何时不调用；初始化 `instructions` 写"每次新对话先调用 get_context"。
- 文件格式：YAML front matter（name / description / aliases / updated）+ 一行一个事实，行尾 `[来源 · 日期]` 由服务端按鉴权身份追加。
- 服务端强制规则：来源后缀由服务端写；remember 前查重，相近则返回候选；写 `profile.md` 需 `confirm=true`；单文件 ≤ 16 KB；拦截证件号、银行卡/账户号、密码、API key。
- 鉴权：CLI 客户端用静态 bearer token（绑定身份）；聊天类 app 用单用户 OAuth 2.1。权限按身份在 `clients.yaml` 里用路径 glob 定义。
- 密钥只放 `.env`（已在 `.gitignore`）；代码仓库与记忆仓库分开。

## 约定

- **测试**：存储层、权限层、HTTP/OAuth 行为都有 pytest 覆盖，改动要同步测试，特别是"无权路径被拒""敏感信息被拦截""中文检索命中""重复 remember 返回候选"。
- **安全功能是产品需求**：敏感信息拦截、鉴权、权限检查、访问日志、限流都要保留。
- **version 并发令牌**：`read` 返回的 `version` 是文件内容哈希，用于乐观并发控制。
- **参数**：业务和运行参数放项目根目录 `memhub.toml` / `memhub.prod.toml`，缺失即报错，不设隐式默认值；密钥从 `.env` 文件直接读取，不依赖进程环境变量注入。
- MCP 规范、MCP Python SDK、各 app 接入要求变化快：涉及时先查最新官方文档（modelcontextprotocol.io、github.com/modelcontextprotocol/python-sdk、PyPI），不凭记忆写。
- 保持工具少而清晰，只做需要的东西。
