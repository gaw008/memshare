# memshare

**English** | [简体中文](README.zh-CN.md)

**One shared memory about you, for all of your AI assistants.**

> The repository is called memshare; the server, CLI and MCP connection are named `memhub`.

If you split your work across AI assistants — Claude Code for coding, Gemini for everyday questions, ChatGPT for research, Grok for social posts — you have probably noticed they don't know each other. The goals, habits and project updates you told one of them are unknown to the next, so you keep repeating yourself.

memhub is a self-hosted remote MCP server that keeps the long-lived facts about you in plain Markdown, readable and writable by any MCP-capable assistant. Think of it as a **filing cabinet shared by all your assistants**: every line records who wrote it and when.

```
Claude / ChatGPT / Gemini / Grok (OAuth)   Claude Code / Gemini CLI / Codex (static token)
                    \                                   /
                     └──── HTTPS /mcp (Streamable HTTP) ─┘
                                     │
                              memhub (this repo)
                  auth → per-identity permissions → 7 MCP tools
                                     │
            Markdown files (single source of truth, one git commit per write)
            SQLite FTS5 index (rebuildable from files, Chinese word segmentation)
```

## Features

- **Markdown is the single source of truth.** One fact per line, easy for humans to read and edit. Every write is a `git commit`, and an hourly timer can push the memory to your own private GitHub repo as a backup.
- **Traceable sources.** The `[gemini · 2026-10-13]` suffix on each line is appended by the server from the authenticated identity; models cannot forge it.
- **A small tool set designed for models.** Fewer tools means every vendor's model uses them correctly. Tool descriptions are the prompt: they say when to call and when not to.
- **Rules enforced server-side.** Duplicate check before writing (near matches come back as candidates so the model uses `update` instead); explicit confirmation to write `profile.md`; 16 KB per-file limit; regex blocking of ID numbers, card numbers, passwords and API keys.
- **Per-identity authorization.** Chat apps use OAuth 2.1 (DCR + PKCE); CLIs use static bearer tokens. Which paths each identity may read or write is set with globs in `clients.yaml`.
- **Daily conversation journal.** Assistants append each finished topic — what you asked, where you got stuck — to one shared file per day, so you can ask an AI to review it with you the next morning.
- **No external services at runtime.** No LLM API calls, no CDN, no remote fonts, no downloads.

## The seven tools

| Tool | Purpose |
| --- | --- |
| `get_context` | Call first in every new conversation. Returns profile and preferences facts plus a `path → description` file catalog, kept around 2K tokens |
| `search` | Full-text search over facts (also matches file descriptions and aliases), with Chinese word segmentation |
| `read` | Full file content plus a `version` (up to 10 files per call); read before changing a fact |
| `remember` | Append a long-lived fact the user explicitly stated; near duplicates return candidates instead of writing |
| `update` | Replace exactly one existing fact, with optimistic concurrency via `version` |
| `forget` | Delete one fact line (never a whole file) |
| `log` | Append "what was asked, where it got stuck, what was decided" for a finished topic to `journal/YYYY-MM-DD.md`; append-only, kept out of the catalog and search |

## What the memory looks like

```
memory/
├── profile.md             identity and long-term goals (writes need confirmation)
├── preferences.md         how you like answers, long-term preferences
├── _inbox.md              facts not yet filed
├── topics/fitness.md      topics: fitness, investing, tech…
├── areas/project-x.md     projects
├── people/friend.md       facts about other people (the subject is always named)
└── journal/2026-10-17.md  daily conversation journal (append-only)
```

```markdown
---
name: fitness
description: Training, weight and diet habits
aliases: [workout, gym]
updated: '2026-10-13'
---
- Runs three times a week [claude-code · 2026-10-13]
- Currently on a muscle-gain program, target weight 70 kg [gemini · 2026-10-14]
```

## Quick start (local)

Requires Python 3.12, [uv](https://docs.astral.sh/uv/) and Git. Every command takes an explicit config file; relative paths resolve against that file's directory, and missing keys are errors — there are no implicit defaults.

```bash
git clone https://github.com/gaw008/memshare.git && cd memshare
uv sync --locked
# Generate random tokens for the two CLI identities (only if .env does not exist yet):
uv run python - <<'PY'
import json, secrets
from pathlib import Path
p = Path('.env')
with p.open('x', encoding='utf-8') as f:
    f.write("STATIC_TOKENS='" + json.dumps({secrets.token_urlsafe(48): app for app in ('claude-code', 'gemini-cli')}) + "'\n")
p.chmod(0o600)
PY
uv run memhub --config memhub.toml init           # create the separate memory git repo (./dev-memory)
uv run memhub --config memhub.toml set-password   # set the OAuth login password (argon2 hash stored in .env)
uv run memhub --config memhub.toml serve          # listens on 127.0.0.1:8787
```

Connect Claude Code locally (use the token mapped to `claude-code` in `.env`):

```bash
claude mcp add --transport http --scope user memhub \
  http://127.0.0.1:8787/mcp --header 'Authorization: Bearer <claude-code-token>'
```

Then start a new session and say "call memhub's get_context first".

## Deploy publicly and connect chat apps

Chat apps such as ChatGPT, Claude (web and mobile), Gemini and Grok can only reach a public HTTPS URL.

1. **Deploy.** See [docs/DEPLOY.md](docs/DEPLOY.md) (Chinese). The reference setup is one Ubuntu server with systemd and a Cloudflare Tunnel (no open inbound ports), with install and update scripts and an hourly backup timer. First replace `mem.example.com` and `YOUR_GITHUB_USER` in `memhub.prod.toml` with your own values.
2. **Connect each app.** See [docs/CLIENTS.md](docs/CLIENTS.md) (Chinese) for Claude, ChatGPT, Gemini, Grok, Claude Code and Gemini CLI. During OAuth, chat apps land on memhub's login page: enter your password and pick "which app is this" — that choice fixes the connection's identity and permissions.
3. **Add custom instructions to each assistant** (see the template below). Not every vendor honors MCP initialize instructions, so custom instructions are the reliable path.

```text
I use a personal memory server called memhub (MCP tools). Rules:
1. At the start of every new conversation, call get_context before answering.
2. For questions about my personal situation (health, projects, family, preferences), search first.
3. When I state a new long-lived fact (goal, habit, decision, project progress, something about a person), call remember; if an existing fact changed, use update.
4. Do not store small talk, your guesses, or your advice (unless I explicitly adopt it).
5. Ask me before writing to profile.
6. When a topic wraps up, call log with one line: what I asked, where I got stuck or what I decided.
```

> After a new tool is added to memhub, some chat apps keep using a cached tool list. Refresh or reconnect the connector in that app's settings and start a new conversation.

## Configuration

| File | Contents |
| --- | --- |
| `memhub.toml` / `memhub.prod.toml` | Dev / production parameters: directories, port, timezone (decides which day a journal entry belongs to), file limit, context token budget, rate limit, allowed hosts, sensitive-data regexes, and more |
| `.env` | Secrets: CLI static tokens and the OAuth password hash. Never committed; see `.env.example` |
| `clients.yaml` | Path globs each identity may read and write. By default every identity can read and write everything |

Authorization management and bulk import:

```bash
uv run memhub --config memhub.toml clients list
uv run memhub --config memhub.toml clients revoke '<client_id>'
uv run memhub --config memhub.toml import --source gemini \
  --path topics/fitness.md --description 'Fitness and training' facts.txt
```

Each non-empty line of the import file is one fact, and goes through the same permission, sensitive-data and duplicate checks as `remember`. `profile.md` cannot be imported.

## Security and limits

- The access log records only time, identity, tool name and paths — never fact contents, search queries or tokens.
- Per-token rate limiting (60 requests per minute by default); excess requests get HTTP 429.
- Sensitive-data blocking only covers the regex patterns in the config. It is not a vault; don't store secrets here.
- An identity is the app label the user picks and binds at login. It does not authenticate that the vendor's client is genuine.
- Single-process write lock plus atomic file replacement; there is no transaction handling for multiple processes or concurrent external edits. After editing Markdown by hand, restart the service to rebuild the index.
- The OAuth database and `.env` are credential state and are not part of the Markdown backup; back up your server state yourself.
- Vendor support as of 2026-10: Claude and ChatGPT let you choose DCR; Gemini's consumer app officially supports DCR but has region, language and account-type restrictions; Grok has not published full OAuth registration details, so verify compatibility yourself. See [docs/AUTH-RESEARCH.md](docs/AUTH-RESEARCH.md) (Chinese).

## Development

```bash
uv run pytest -q
# After dependency changes, export requirements.txt for the server:
uv export --no-hashes --no-dev --no-emit-project --output-file requirements.txt
```

Tests keep their temporary files and memory repos in `.test-tmp/` and never touch real memory. Stack: Python 3.12, the official [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) (Streamable HTTP at `/mcp`, health check at `/healthz`), SQLite FTS5, jieba.

## License

[MIT](LICENSE)
