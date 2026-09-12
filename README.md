# KiroCrew OpenCode ACP Backend

Run [KiroCrew](https://github.com/kirodotdev/KiroCrew) with
[OpenCode](https://opencode.ai) as the ACP agent backend — no fork, no source
patches. Runtime monkey-patching, inspired by
[lenovo1996/KiroCrew-OpenAI-Compatible](https://github.com/lenovo1996/KiroCrew-OpenAI-Compatible).

KiroCrew is pinned in `[tool.uv.sources]` in `pyproject.toml` (no version is
repeated here — it changes independently of this file). The Docker image
installs the matching upstream **release wheel** rather than the git tag — see
[Docker](#docker).

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
│   ├── install.py                # Patches 1, 4, 5, 7 (types, factory, bg sessions, kiro-cli gate)
│   └── provider.py               # Patches 2, 3, 6 (client, provider, dispatch)
└── tests/
    ├── test_config.py            # Env-var handling
    ├── test_dispatch.py          # raw_params refresh logic (mocked kiro_crew)
    ├── test_install.py           # install() wiring (mocked kiro_crew)
    └── test_kirocrew_contract.py # Patch targets vs the REAL installed kirocrew
```

## What gets patched (7 patches)

| # | Target | What |
|---|--------|------|
| 1 | `acp.types` | Add `ACP_BACKEND_OPENCODE` constant + membership in `ACP_BACKENDS_KNOWN` |
| 2 | `acp.client.AcpClient` | `_is_opencode`, `_start_process` (launch helper — upstream has none), `_spawn` (`opencode acp --cwd`), `_initialize_session` (numeric protocol), `_extract_tool_call_refinement` (raw_params fix), `supports_steer`, `send_command`, `stream_command`, `_reject_unknown_server_request` |
| 3 | `providers.acp.AcpProvider` | `is_opencode_backend`, route through AcpClient (not AcpRuntime), skip kiro-cli overlays |
| 4 | `config.loader` | Factory injects `acp_backend=opencode` into the provider |
| 5 | `acp_backends` + `config.loader.KiroCrewConfig` | Register `opencode` as selectable, and name it in `agent.acp_backend` on every config load — bg sessions read the *config*, not the provider, and `''` means kiro-cli |
| 6 | `acp._dispatch` | `raw_params_cache` refresh — **self-retiring**, see below. Skipped on 0.4.1 |
| 7 | `kiro_prerequisite.KiroPrerequisiteService` | Force `assume_ready=True` at construction — the kiro-cli readiness gate has nothing to probe here |

`install()` logs which patches applied:

```
opencode_provider: added ACP_BACKEND_OPENCODE to acp.types
opencode_provider: added opencode to ACP_BACKENDS_KNOWN
opencode_provider: provider factory patched ✅
opencode_provider: bg session routing patched ✅
opencode_provider: kiro-cli readiness gate bypassed ✅
opencode_provider: AcpClient patched ✅
opencode_provider: AcpProvider patched ✅
opencode_provider: upstream already refreshes raw_params_cache — skipping raw_params fix
```

## Background sessions (patch 5)

Auto-title and link-summary run as background sessions, and those pick their
harness from `cfg.agent.acp_backend` — **the config, never the provider** our
factory patch injects. A value of `''` is in `ACP_BACKENDS_ACP_RUNTIME` (with
`kas`), so those sessions went to the multiplexed kiro-cli runtime and died with
`AcpRuntimeError: kiro-cli not found`, one per session, while interactive
sessions worked. `KIROCREW_ACP_BACKEND` cannot reach that decision: upstream
never reads the variable, so it only ever reached our own patches.

Patch 5 therefore does two things, both upstream's own extension points:
`register_selectable_backend("opencode")` makes the id nameable in
`agent.acp_backend` (and stops `resolve_selected_backend` coercing it back to
kiro inside every `KiroCrewConfig.load()`), and wrapping `KiroCrewConfig.load`
sets the field, which is the single constructor for the config object the
gateway, the session manager and the dashboard all share. Staying out of
`ACP_BACKENDS_ACP_RUNTIME` is the other half — that set is what
`_bg_backend_supports_runtime()` tests membership in.

## The kiro-cli readiness gate (patch 7)

Upstream derives readiness from the kiro-cli binary, and this image deliberately
has none — opencode *is* the backend. The probe can therefore never pass, and on
a fresh data home that gates a container working exactly as designed:

- the dashboard SPA branches on `ready` / `initial_setup_complete` and renders
  its "install kiro-cli" first-run gate instead of the app;
- `dashboard.kiro_readiness.reject_if_kiro_unverified` answers **503** for
  `/api/models`, the destructive reruns (regenerate, edit-resend, rewind) and
  `POST /v1/chat/completions` — the OpenAI-compatible endpoint, so an API client
  sees nothing but `kiro_prerequisite_required`;
- the agent-spec overlay reports a repair gate for specs this home never had.

There is no client-side skip: a payload that says "not ready" wins over the
`kirocrew:kiro-setup-complete` localStorage marker. The supported switch is
upstream's own `assume_ready`, which this patch forces at construction. The
service then reports an established, authenticated install without probing, and
every other consumer of the flag is a no-op (identity probe, session retirement,
`kiro-cli update`). Scoped by `install()`, so a kiro-cli gateway keeps the real
fail-closed probe.

`--test-mode` would reach the same state but is not usable: it also forces
`--approval reads`, which would block the agent's writes.

## Quick start

### Docker

```bash
docker build -t kirocrew-opencode .
docker run -d \
  -e KIROCREW_ACP_BACKEND=opencode \
  -v /path/to/opencode.json:/config/opencode.json:ro \
  -v /path/to/auth.json:/root/.local/share/opencode/auth.json:ro \
  -p 5476:5476 \
  kirocrew-opencode
```

The dashboard is then at `http://localhost:5476/` (5476 is KiroCrew's own
default port, not 3000).

**The UI comes from the wheel, not the git tag.** The dashboard SPA is served
from `<site-packages>/kiro_crew/static/dist`, and that directory exists **only
in upstream's release wheel** — `src/kiro_crew/static/dist` is a gitignored
build artifact, so the pinned git tag's tree has Python but no frontend. That is
why the Dockerfile installs the versioned wheel from the GitHub release (and
`uv pip install --no-deps` for this package, so uv cannot resolve kirocrew from
`[tool.uv.sources]` and clobber it). Installing from the git source instead
yields a working gateway that serves a "frontend not built" page; the Dockerfile
and the CI smoke test both assert `static/dist/index.html` is present.

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

Real breakages have already happened:

- `_start_process` was called but has never existed in any KiroCrew version —
  the OpenCode spawn path was dead until the provider defined it.
- `_spawn` called the synchronous `wrap_argv()` while being a coroutine. That
  helper refuses to run when a loop is running, so **every** spawn raised and
  only surfaced as a generic "Failed to create background session" line in the
  gateway log. Awaiting `wrap_argv_async()` is what fixed it.
- `parse_session_update` gained a `cache_scope` keyword in 0.4.1. The patch
  re-declared upstream's signature, so upstream's own callers hit
  `TypeError` on every `tool_call_update` frame.
- background sessions picked kiro-cli, not opencode, and failed one per session
  with `kiro-cli not found` — see [patch 5](#background-sessions-patch-5). They
  read `agent.acp_backend` from the config, and upstream never reads
  `KIROCREW_ACP_BACKEND`, so our provider patch was never in that decision.

`tests/test_kirocrew_contract.py` exists to catch exactly this. Unlike the
other test modules — which fabricate `kiro_crew` via `sys.modules` and
therefore pass no matter what upstream does — it imports the installed
KiroCrew and checks:

1. every module/class symbol the provider reads or replaces still exists
2. every callable the provider wraps still accepts the parameters upstream
   declares (signatures captured before `install()`, diffed after)
3. the opencode spawn still *runs* — `_spawn` is called for real inside an
   event loop, with the binary lookup and the launch stubbed. Signatures are
   blind to this class of bug: `wrap_argv` and `wrap_argv_async` take
   identical arguments, and only the async one may be called from a coroutine.
4. the prerequisite service still reports a *usable* install against an empty
   data home — both the payload the SPA branches on and the predicates behind
   the 503s. A kiro-cli-shaped gate is the one thing this image can never
   satisfy by probing, so it has to be answered some other way, and this asserts
   the answer.
5. the loaded config still names our backend and the `_bg` predicate still keeps
   background sessions off the multiplexed runtime — read through upstream's own
   `_bg_runtime_backends()`, not re-derived here, so a bump that makes the
   runtime accept us turns the test red.

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
  readiness, not the ACP backend process. Patch 7 answers that readiness gate as
  satisfied instead (see [above](#the-kiro-cli-readiness-gate-patch-7)).
- **No model catalog probe** — OpenCode advertises models via
  `session/new` `configOptions`, which KiroCrew reads natively.
- **No session sharing** — OpenCode is one process per session (like
  claude), not multiplexed (like kiro-cli).
- **No full gateway boot in CI** — the Docker workflow builds the image, loads
  it, and smoke-tests it: the `gateway`/`kirocrew`/`opencode` binaries resolve,
  `kirocrew --version` runs, `install()` applies the patches for real, and
  `kiro_crew.cli` imports. It does **not** start the gateway or bind a port —
  that needs config and credentials, which is how smoke tests turn flaky.
