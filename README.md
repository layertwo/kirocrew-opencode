# KiroCrew OpenCode ACP Backend

Run [KiroCrew](https://github.com/kirodotdev/KiroCrew) with
[OpenCode](https://opencode.ai) as the ACP agent backend — no fork, no source
patches. Runtime monkey-patching, inspired by
[lenovo1996/KiroCrew-OpenAI-Compatible](https://github.com/lenovo1996/KiroCrew-OpenAI-Compatible).

## How it works

```
KiroCrew Gateway
  ↓
opencode_provider.install()  ← monkey-patches AcpClient + factory before boot
  ↓
AcpProvider  ← KiroCrew's own provider, patched to spawn opencode
  ↓
opencode acp --cwd <work_dir>  ← real opencode subprocess per session
```

The package reuses KiroCrew's own `AcpClient` for all ACP event parsing,
tool-call extraction, and permission flows — it just changes *what gets
spawned* (opencode instead of kiro-cli) and handles the protocol differences
(numeric version, no `set_mode`, no `_kiro.dev/*`, no session sharing).

## vs the OpenAI-compatible fork

| | This package | lenovo1996's OpenAI-compatible |
|---|---|---|
| **Agent** | Real `opencode acp` subprocess (full tools, MCP, skills) | No agent — reimplements tool execution in Python |
| **Transport** | ACP JSON-RPC over stdio | HTTP to `/v1/chat/completions` |
| **Patching** | Runtime monkey-patch (no source diffs) | Runtime monkey-patch (no source diffs) |
| **Model** | Whatever opencode is configured with | Any OpenAI-compatible endpoint |

## vs the source-patch fork (previous approach)

The `patches/` directory contains the original source-diff patch set for
reference and for an upstream PR. The actual delivery is now the
`opencode_provider/` package — no `git apply`, no fork, survives upstream
KiroCrew upgrades.

## File structure

```
kirocrew-opencode-fork/
├── gateway.py                    # Entry point: install() then kirocrew gateway
├── pyproject.toml                # pip-installable package
├── opencode_provider/
│   ├── __init__.py               # Public API: install(), is_installed()
│   ├── _config.py                # Env vars, ACP_BACKEND_OPENCODE constant
│   └── install.py                # 6 monkey-patches (types, client, provider, factory, bg, dispatch)
└── Dockerfile                    # pip install kirocrew + opencode_provider, no source patching
```

## What gets patched (6 patches)

| # | Target | What |
|---|--------|------|
| 1 | `acp.types` | Add `ACP_BACKEND_OPENCODE` constant + membership in `ACP_BACKENDS_KNOWN` |
| 2 | `acp.client.AcpClient` | `_is_opencode`, `_spawn` (opencode acp --cwd), `_initialize_session` (numeric protocol), `_extract_tool_call_refinement` (raw_params fix), `supports_steer`, `send_command`, `stream_command`, `_reject_unknown_server_request` |
| 3 | `providers.acp.AcpProvider` | `is_opencode_backend`, route through AcpClient (not AcpRuntime), skip kiro-cli overlays |
| 4 | `config.loader` | Factory injects `acp_backend=opencode` into the provider |
| 5 | `session.SessionManager` | `_bg_provider_is_kiro → False` (bg sessions route through factory, not kiro-cli) |
| 6 | `acp._dispatch` | `_build_tool_refinement_event` raw_params_cache refresh (root-cause fix for deny-by-default; benefits ALL backends) |

## Quick start

### Docker

```bash
docker build -t kirocrew-opencode .
docker run -d \
  -e KIROCREW_ACP_BACKEND=opencode \
  -e OPENCODE_AUTH_CONTENT='{"opencode":[{"token":"..."}]}' \
  -v /path/to/opencode.json:/config/opencode.json:ro \
  -p 3000:3000 \
  kirocrew-opencode
```

### Manual

```bash
pip install kirocrew
pip install -e /path/to/kirocrew-opencode-fork
KIROCREW_ACP_BACKEND=opencode gateway
```

## Environment variables

| Var | Default | Description |
|-----|---------|-------------|
| `KIROCREW_ACP_BACKEND` | `""` (kiro-cli) | Set to `opencode` to activate this provider |
| `OPENCODE_BIN` | `opencode` (PATH lookup) | Override the opencode binary path |
| `OPENCODE_CONFIG` | `/config/opencode.json` | Path to opencode.json config |

## Upgrading KiroCrew

Just `pip install --upgrade kirocrew`. The monkey-patches target stable
seams (`AcpClient._spawn`, `AcpClient._initialize_session`,
`AcpProvider.start`, `KiroCrewConfig.create_provider_factory`) that haven't
changed in years. If an upstream rename breaks a patch, the `install()`
call logs a warning and continues — KiroCrew falls back to kiro-cli.

## The raw_params_cache bug fix

Patch 6 (the `_dispatch` fix) is a pure bugfix that benefits ALL backends
(kiro-cli, claude, opencode). When an agent streams a tool call in two
frames (initial `tool_call` with empty `rawInput`, then `tool_call_update`
with the populated dict), the refinement handler refreshed `shell_cache`
and `tool_input_cache` but never `raw_params_cache`. So the permission gate
couldn't recover the command and deny-by-default fired for shell tools.
This is not OpenCode-specific — it affected any agent that streams tool
calls in two frames. The fix could be upstreamed directly.

## What's NOT here (deliberately)

- **No tool reimplementation** — opencode's real bash, file edit, MCP
  servers, and skills all work as designed. lenovo1996 reimplements 13 tool
  handlers in Python; we don't.
- **No `whoami`/`--version` probe** — KiroCrew only probes kiro-cli for
  readiness, not the ACP backend process.
- **No model catalog probe** — OpenCode advertises models via
  `session/new` `configOptions`, which KiroCrew reads natively.
- **No session sharing** — OpenCode is one process per session (like
  claude), not multiplexed (like kiro-cli).