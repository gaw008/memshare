# Connecting clients to memhub

Vendor entry points were checked on **2026-10-13**; their UIs may have changed since, so follow what your app actually shows. Below, **`https://mem.example.com/mcp`** stands for your public MCP URL — replace it with your own domain. Protocol and SDK research is in [AUTH-RESEARCH.md](AUTH-RESEARCH.md).

## Common preparation

1. Deploy the service and its Tunnel as described in [DEPLOY.md](DEPLOY.md). Confirm the public metadata is reachable and that an unauthenticated `/mcp` request returns 401 with a `WWW-Authenticate` header.
2. Set the authorization password once on the server:

   ```bash
   sudo -u memhub /srv/memhub/app/.venv/bin/memhub \
     --config /srv/memhub/app/memhub.prod.toml set-password
   ```

3. Chat apps use **OAuth authorization code + PKCE S256 + DCR**. On the login page, enter the password and choose the current app under "Which app is this"; after the redirect, call `get_context` in a new conversation. CLIs use their own static tokens.
4. The server applies `clients.yaml` permissions for the identity chosen at login. A chat app cannot act as a CLI identity, and the DCR client name does not decide permissions.

Permissions: the default `clients.yaml` lets every identity read and write every path. Tighten it as needed (for example, let the assistant that writes social posts read only `topics/social-media.md`).

## Claude (web, Desktop, mobile, Cowork)

1. In a personal account, open **Customize → Connectors → + Add → Add custom connector**. On Team/Enterprise, an Owner with the right permissions adds it under Organization settings → Connectors, then members Connect.
2. Name it `memhub` and set the remote URL to `https://mem.example.com/mcp`.
3. For Authentication choose **Sign in now**; for the OAuth client choose **Register automatically**. This server uses DCR, so do not pick the published-identity mode that relies on CIMD.
4. Complete the password page and choose `claude`; enable memhub in a new conversation under `+ → Connectors`.

The hosted callback is `https://claude.ai/api/mcp/auth_callback`; the server receives and validates it through DCR, so no callback needs to be added in code. Claude supports DCR, PKCE S256, Streamable HTTP and refresh tokens; this server rotates refresh tokens. Official guide: [Custom connectors](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp); technical requirements: [Authentication](https://claude.com/docs/connectors/building/authentication), [Transport](https://claude.com/docs/connectors/building).

## ChatGPT (custom MCP plugin / developer mode app)

The official docs currently describe two entry points; use whichever your account shows:

1. **New plugin entry**: open ChatGPT Plugins, click `+ → Add custom MCP server`, enter a name and the Server URL, choose OAuth, and explicitly choose **DCR** for client registration. Create it as a plugin as prompted, install it, and use `@memhub` in a conversation.
2. **Developer mode app entry**: users whose workspace allows it enable developer mode in **Settings → Apps → Advanced Settings**; then Apps → Create, enter the URL and OAuth settings, Scan Tools, complete login (choose identity `chatgpt`), and Create. Business / Enterprise / Edu admins can also do this from Workspace settings → Apps → Create.

The callback may be `https://chatgpt.com/connector/oauth/{callback_id}`, or the stable callback of an existing connection. Use the exact URI from the registration request; never reuse another connection's callback. CIMD is OpenAI's recommended method, but DCR is still supported; this project does not advertise CIMD. Staying connected depends on refresh tokens being issued and on the `offline_access` capability in the authorization server metadata. Official: [Add custom MCP server](https://developers.openai.com/api/docs/guides/custom-mcp-server), [OAuth / callback](https://developers.openai.com/plugins/build/auth), [Developer mode / refresh / plan availability](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt).

The developer mode help page says full MCP write access is for Business / Enterprise / Edu; on Pro this entry may be read/fetch only. Even if the server allows writes, your ChatGPT plan or workspace may not expose write tools. With the new plugin entry, verify against the permissions the page actually grants.

## Gemini (personal web and mobile app)

1. Go to **gemini.google.com → Settings → Connected Apps**; if that entry is missing, go to Personal Intelligence → Connected Apps first.
2. Under Custom apps click **Add a custom app**, enter the MCP URL, and click Next. Because the server offers DCR, leave the manual client credentials under Advanced features empty.
3. Complete login (choose identity `gemini`), return to Gemini, and type `@` in a new conversation to select memhub.
4. Per the official help, apps can only be added on the web; once added they work on web and mobile. The feature currently requires a US region, age 18+, a personal Google Account, Keep Activity turned on, and an English interface.

Source: [Gemini Apps help](https://support.google.com/gemini/answer/17209137?hl=en). This consumer help page does not publish a fixed callback, CIMD support, or a refresh-token requirement; the server offers standard DCR, PKCE and refresh, and the callback comes from the registration request. **Do not** use the Gemini Enterprise admin entry instead of personal Connected Apps; Enterprise Business has its own StreamableHTTP-only requirement and different steps: [Enterprise guide](https://support.google.com/g/answer/17106276?hl=en).

## Grok

1. At **grok.com/connectors → New Connector → Custom**, enter the MCP URL.
2. Complete authentication as the page asks; if it offers a registration method, choose DCR / automatic registration. On the password page choose identity `grok`.
3. In a new conversation, call memhub's `get_context` and check that you can see all files.

The official docs confirm the custom connector entry, the public reachability requirement, and that Streamable HTTP works through a Tunnel: [Connectors](https://docs.x.ai/grok/connectors), [Tunneling](https://docs.x.ai/grok/connectors/custom-mcp-tunneling). They do not currently publish DCR/CIMD details, a fixed callback, or refresh requirements. This server offers DCR, but **real-world Grok authorization compatibility still needs to be verified after deployment**. If your account only offers a pre-registered client flow, note the fields and callback the page asks for and evaluate from there; local protocol tests do not prove Grok works.

## Claude Code

Read the `claude-code` static token privately from `/srv/memhub/state/.env` (created by the installer) and replace the placeholder:

```bash
claude mcp add --transport http --scope user memhub \
  https://mem.example.com/mcp \
  --header 'Authorization: Bearer <claude-code-token>'
claude mcp list
```

Add the instruction template below to your global `CLAUDE.md`. Start a new session, check the tools with `/mcp`, then call `get_context`. The static token's identity is `claude-code`; no OAuth identity picker is involved. Official: [Claude Code MCP](https://code.claude.com/docs/en/mcp).

## Gemini CLI

With the `gemini-cli` static token, use the official HTTP transport and header options:

```bash
gemini mcp add --transport http --scope user \
  --header 'Authorization: Bearer <gemini-cli-token>' \
  memhub https://mem.example.com/mcp
gemini mcp list
```

Or merge this snippet into your own `~/.gemini/settings.json` (keeping your other settings):

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

Put the instruction template in your global `GEMINI.md`; verify with `/mcp list` in a new session. Official: [Gemini CLI MCP](https://geminicli.com/docs/tools/mcp-server/).

## Custom instruction template for every assistant

```text
I use a personal memory server called memhub (MCP tools). Rules:
1. At the start of every new conversation, call get_context before answering.
2. For questions about my personal situation (health, projects, family, preferences), search first.
3. When I state a new long-lived fact (goal, habit, decision, project progress, something about a person), call remember; if an existing fact changed, use update.
4. Do not store small talk, your guesses, or your advice (unless I explicitly adopt it).
5. Ask me before writing to profile.
6. When a topic wraps up, call log with one line: what I asked, where I got stuck or what I decided.
```

The template cannot change server-side permissions; only `clients.yaml` does. Tool descriptions and MCP initialize instructions nudge the model but cannot guarantee every product calls the tools on its own, so on first use explicitly ask it to "use memhub's get_context first".

## Managing and verifying authorizations

```bash
sudo -u memhub /srv/memhub/app/.venv/bin/memhub \
  --config /srv/memhub/app/memhub.prod.toml clients list
sudo -u memhub /srv/memhub/app/.venv/bin/memhub \
  --config /srv/memhub/app/memhub.prod.toml clients revoke '<client_id>'
```

For each app, check that authorization completes, `get_context` succeeds, new facts from apps that can write carry the identity you chose, access survives token expiry through refresh, and access is refused after revoke. Across apps: have Claude Code remember a fact you actually confirmed, then have Gemini find it with search; every source suffix should match the identity that wrote it.
