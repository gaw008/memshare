# memhub · Development conventions

A remote MCP server that gives all of a user's AI assistants one shared memory. This file is for people and AI coding assistants working in this repository; user documentation is in `README.md`.

## Key points

- Memory is Markdown files in a separate git repository, the single source of truth. The SQLite FTS5 index can always be rebuilt from the files (full build at startup, incremental after each write).
- Python 3.12 + the official MCP Python SDK, Streamable HTTP at `/mcp`; health check at `/healthz`.
- 7 tools: `get_context` `search` `read` `remember` `update` `forget` `log`. `log` only appends to `journal/YYYY-MM-DD.md` (the day is taken in the configured `timezone`), with a `[identity · HH:MM]` suffix. Journal files stay out of the `get_context` catalog and the search index, cannot be changed by remember/update/forget, and are readable with `read`. `get_context` returns a compact shape (a `path → description` catalog plus fact lines without source suffixes) and an `instructions` field reminding the model to call `log` when a topic wraps up.
- Tool descriptions are written in English and say when to call and when not to; the initialize `instructions` tell the model to call get_context at the start of every new conversation.
- File format: YAML front matter (name / description / aliases / updated) + one fact per line; the `[source · date]` suffix is appended by the server from the authenticated identity.
- Server-enforced rules: the server writes source suffixes; remember checks for duplicates and returns near matches as candidates; writing `profile.md` needs `confirm=true`; ≤ 16 KB per file; ID numbers, bank card/account numbers, passwords and API keys are blocked.
- Auth: CLI clients use static bearer tokens bound to an identity; chat apps use single-user OAuth 2.1. Per-identity permissions are path globs in `clients.yaml`.
- Secrets live only in `.env` (already in `.gitignore`); the code repository and the memory repository are separate.

## Conventions

- **Tests**: storage, permissions and HTTP/OAuth behavior are covered by pytest; change tests together with code, especially "unauthorized paths are refused", "sensitive data is blocked", "Chinese search hits", and "duplicate remember returns candidates".
- **Security features are product requirements**: keep sensitive-data blocking, authentication, permission checks, access logging and rate limiting.
- **version concurrency token**: the `version` returned by `read` is a hash of the file content, used for optimistic concurrency control.
- **Parameters**: business and runtime parameters live in the root `memhub.toml` / `memhub.prod.toml`; missing values are errors, with no implicit defaults. Secrets are read directly from the `.env` file, not injected through process environment variables.
- The MCP spec, the MCP Python SDK and each app's connection requirements change quickly: check the latest official docs (modelcontextprotocol.io, github.com/modelcontextprotocol/python-sdk, PyPI) instead of relying on memory.
- Keep the tool set small and clear; build only what is needed.
- The project's default language is English; `README.zh-CN.md` is the maintained Chinese translation of the README.
