# KiroCrew OpenCode ACP Backend

Run [KiroCrew](https://github.com/kirodotdev/KiroCrew) with
[OpenCode](https://opencode.ai) as the ACP agent backend — no fork, no source
patches. Runtime monkey-patching, inspired by
[lenovo1996/KiroCrew-OpenAI-Compatible](https://github.com/lenovo1996/KiroCrew-OpenAI-Compatible).

Pinned to KiroCrew **v0.4.1** (see `[tool.uv.sources]` in `pyproject.toml`).

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

## File structure

```
kirocrew-opencode/
├── gateway.py                    # Entry point: install() then kirocrew gateway
├── pyproject.toml                # Package metadata; pins kirocrew via [tool.uv.sources]
├── Dockerfile                    # uv pip install kirocrew (from git) + opencode_provider
├── opencode_provider/
│   ├── __init__.py               # Public API: install(), is_installed()
│   ├── _config.py                # Env vars, ACP_BACKEND_OPENCODE constant
│   ├── install.py                # Patches 1, 4, 5 (types, factory, bg sessions)
│   └── provider.py               # Patches 2, 3, 6 (client, provider, dispatch)
└── tests/
    ├── test_config.py            # Env-var handling
    ├── test_dispatch.py          # raw_params refresh logic (mocked kiro_crew)
    ├── test_install.py           # install() wiring (mocked kiro_crew)
    └── test_kirocrew_contract.py # Patch targets vs the REAL installed kirocrew
```

## What gets patched (6 patches)

| # | Target | What |
|---|--------|------|
| 1 | `acp.types` | Add `ACP_BACKEND_OPENCODE` constant + membership in `ACP_BACKENDS_KNOWN` |
| 2 | `acp.client.AcpClient` | `_is_opencode`, `_start_process` (launch helper — upstream has none), `_spawn` (`opencode acp --cwd`), `_initialize_session` (numeric protocol), `_extract_tool_call_refinement` (raw_params fix), `supports_steer`, `send_command`, `stream_command`, `_reject_unknown_server_request` |
| 3 | `providers.acp.AcpProvider` | `is_opencode_backend`, route through AcpClient (not AcpRuntime), skip kiro-cli overlays |
| 4 | `config.loader` | Factory injects `acp_backend=opencode` into the provider |
| 5 | `session.SessionManager` | `_bg_provider_is_kiro → False` (bg sessions route through factory, not kiro-cli) |
| 6 | `acp._dispatch` | `raw_params_cache` refresh — **self-retiring**, see below. Skipped on 0.4.1 |

`install()` logs which patches applied:

```
opencode_provider: added ACP_BACKEND_OPENCODE to acp.types
opencode_provider: added opencode to ACP_BACKENDS_KNOWN
opencode_provider: provider factory patched ✅
opencode_provider: bg session routing patched ✅
opencode_provider: AcpClient patched ✅
opencode_provider: AcpProvider patched ✅
opencode_provider: upstream already refreshes raw_params_cache — skipping raw_params fix
```

## Quick start

### Docker

```bash
docker build -t kirocrew-opencode .
docker run -d \
  -e KIROCREW_ACP_BACKEND=opencode \
  -v /path/to/opencode.json:/config/opencode.json:ro \
  -v /path/to/auth.json:/root/.local/share/opencode/auth.json:ro \
  -p 3000:3000 \
  kirocrew-opencode
```

**Authentication.** OpenCode reads credentials from
`~/.local/share/opencode/auth.json` (the container runs as root, so
`/root/.local/...`), or via `{env:VAR}` substitution inside `opencode.json` —
see [the OpenCode config docs](https://opencode.ai/docs/config/). This repo
does **not** implement an `OPENCODE_AUTH_CONTENT` variable; nothing reads it,
and OpenCode does not define it either. Mount the file, or use `{env:...}`.

### Manual

Requires [uv](https://docs.astral.sh/uv/) — KiroCrew is pinned as a git
dependency in `[tool.uv.sources]`, which `pip` ignores. Plain
`pip install -e .` fails with `No matching distribution found for kirocrew`
because KiroCrew is not published to PyPI.

```bash
uv sync
KIROCREW_ACP_BACKEND=opencode uv run gateway
```

## Environment variables

| Var | Default | Description |
|-----|---------|-------------|
| `KIROCREW_ACP_BACKEND` | `""` (kiro-cli) | Set to `opencode` to activate this provider |
| `OPENCODE_BIN` | `opencode` (PATH lookup) | Override the opencode binary path |
| `OPENCODE_CONFIG` | `/config/opencode.json` | Path to opencode.json config (set in the Dockerfile) |

`install()` is a no-op unless `KIROCREW_ACP_BACKEND=opencode`.

## Development

```bash
uv sync --extra dev
uv run pytest tests/ -v
```

## Upgrading KiroCrew

The pin lives in `[tool.uv.sources]` in `pyproject.toml`; Renovate opens a PR
when a new tag ships.

**A bump is not automatically safe, and failures are loud, not graceful.**
`install()` has no error handling around its patches: several read the upstream
attribute before replacing it, so an upstream rename raises `AttributeError`.
`gateway.py` calls `install()` at import time, *before* `from kiro_crew.cli
import main` — so that exception takes the whole gateway down. There is no
fallback to kiro-cli.

Two real breakages have already happened:

- `_start_process` was called but has never existed in any KiroCrew version —
  the OpenCode spawn path was dead until the provider defined it.
- `parse_session_update` gained a `cache_scope` keyword in 0.4.1. The patch
  re-declared upstream's signature, so upstream's own callers hit
  `TypeError` on every `tool_call_update` frame.

`tests/test_kirocrew_contract.py` exists to catch exactly this. Unlike the
other test modules — which fabricate `kiro_crew` via `sys.modules` and
therefore pass no matter what upstream does — it imports the installed
KiroCrew and checks:

1. every module/class symbol the provider reads or replaces still exists
2. every callable the provider wraps still accepts the parameters upstream
   declares (signatures captured before `install()`, diffed after)

CI runs pytest on every PR and `uv sync` installs whatever version the PR pins,
so Renovate bumps are validated automatically. **A red contract test means the
bump will break at runtime — do not merge it.**

Not covered: instance attributes set in `AcpClient.__init__` (`_extra_env`,
`_work_dir`, `_tool_call_params`, …), which would need a constructed client.

## The raw_params_cache bug fix

Patch 6 was a pure bugfix that benefited ALL backends. When an agent streams a
tool call in two frames (initial `tool_call` with empty `rawInput`, then
`tool_call_update` with the populated dict), KiroCrew 0.3.0's refinement
handler refreshed `shell_cache` and `tool_input_cache` but never
`raw_params_cache` — so the permission gate couldn't recover the command and
deny-by-default fired for shell tools.

**KiroCrew 0.4.1 fixes this upstream**, storing the entry under a
session-scoped key. Applying the patch there would write a second, unscoped
key that nothing reads, so `patch_dispatch_raw_params` probes upstream's
behaviour and skips itself when the refresh already happens:

| KiroCrew | upstream alone | patch |
|---|---|---|
| 0.3.0 | `{}` | applies → `{'t1': {...}}` |
| 0.4.1 | `{'sess-abc\|t1': {...}}` | skipped |

The probe is a capability check, not a version check, so the patch retires
itself whenever upstream fixes this — including on backports.

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
- **No container smoke test** — CI builds the image but never runs it, so
  "the build is green" does not mean the gateway starts.
