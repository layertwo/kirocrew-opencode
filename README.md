# kirocrew-opencode

The official [KiroCrew](https://github.com/kirodotdev/KiroCrew) image with the
[OpenCode](https://opencode.ai) binary added, for running KiroCrew on its native
`opencode` agent backend.

## What the image contains

| Layer | Source |
|---|---|
| KiroCrew gateway, entrypoint, kiro-cli | `ghcr.io/kirodotdev/kirocrew` (unchanged) |
| `opencode` binary | `/usr/local/bin/opencode`, from the OpenCode GitHub release |
| `KIRO_API_KEY` | A placeholder value (see below) |

Entrypoint, user (`kirocrew`, uid 1000), port (`5476`) and data home
(`/home/kirocrew`) are inherited from the upstream image.

### `KIRO_API_KEY` placeholder

KiroCrew checks that kiro-cli is signed in (`kiro-cli whoami`) before serving
`/api/models`, regenerate, edit-resend, rewind, session usage and
`/v1/chat/completions`, regardless of which agent backend is selected. `whoami`
exits 0 whenever `KIRO_API_KEY` is set, so the placeholder satisfies that check.
KiroCrew strips the key from the environment of non-kiro agents, so OpenCode
never sees it.

The upstream entrypoint copies the value into the data home's `.env` on every
start. To remove it, delete the `ENV` line **and** the `KIRO_API_KEY=` line from
that `.env`.

## Configuration

Set in KiroCrew's `config.json` (dashboard: Settings → Developer → Agent Backend,
or the CLI):

```bash
kirocrew config set agent.acp_backend opencode
kirocrew config set agent.model ollama-cloud/gpt-oss:120b   # any opencode provider/model id
```

Pin `agent.model`: on 0.7.x the model picker lists kiro-cli's catalog, not
OpenCode's.

Environment:

| Variable | Purpose |
|---|---|
| `OPENCODE_AUTH_CONTENT` | OpenCode credentials as JSON, e.g. `{"ollama-cloud":{"type":"api","key":"..."}}` |
| `OPENCODE_CONFIG` | Path to an `opencode.json` (providers, plugins, permissions) |
| `OPENCODE_BIN` | Override the opencode binary path (optional) |

KiroCrew forces OpenCode's `permission` to `ask` so tool calls route through its
approval gate; per-tool rules in `opencode.json` are kept.

## Versions

| Component | Pinned in | Updated by |
|---|---|---|
| KiroCrew | `FROM` in `Dockerfile` | Renovate (docker) |
| OpenCode | `OPENCODE_VERSION` in `Dockerfile` | Renovate (`customManagers:dockerfileVersions`) |

Images are published to `ghcr.io/layertwo/kirocrew-opencode` as `latest` and
`<kirocrew version>` on every push to `main`. Builds are `linux/amd64` only.
