# Dockerfile for KiroCrew with the OpenCode ACP backend (runtime patch approach).
#
# No source patching — KiroCrew is pip-installed from PyPI, and the
# opencode_provider package monkey-patches it at runtime before the
# gateway boots. Inspired by lenovo1996/KiroCrew-OpenAI-Compatible.
#
# Build:
#   docker build -t kirocrew-opencode .
#
# Run (mount config + credentials; there is no OPENCODE_AUTH_CONTENT var —
#   the Dockerfile sets OPENCODE_CONFIG=/config/opencode.json and opencode reads
#   auth.json from ~/.local/share/opencode/auth.json):
#   docker run -e KIROCREW_ACP_BACKEND=opencode \
#     -v $PWD/opencode.json:/config/opencode.json:ro \
#     -v $PWD/auth.json:/root/.local/share/opencode/auth.json:ro \
#     -p 5476:5476 kirocrew-opencode

# ─────────────────────────────────────────────────────────────
# Stage 1: install opencode (Node CLI)
# ─────────────────────────────────────────────────────────────
FROM node:24-slim AS opencode-stage

RUN npm install -g opencode-ai@1.18.21

# ─────────────────────────────────────────────────────────────
# Stage 2: install KiroCrew + opencode_provider (uv)
# ─────────────────────────────────────────────────────────────
FROM python:3.14-slim AS kirocrew-stage

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install uv (fast Python package installer/resolver).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy opencode binary from stage 1.
COPY --from=opencode-stage /usr/local/lib/node_modules /usr/local/lib/node_modules
COPY --from=opencode-stage /usr/local/bin/opencode /usr/local/bin/opencode

# Copy the opencode_provider package + lockfile, then uv install.
COPY . /opt/opencode_provider/
WORKDIR /opt/opencode_provider

# KiroCrew comes from the upstream RELEASE WHEEL, not from the git tag it is
# pinned to in pyproject.toml — and that distinction is exactly what decides
# whether the image has a dashboard UI.
#
# The tag's source tree has no src/kiro_crew/static/dist. The dashboard's React
# build is a gitignored artifact, staged in only when upstream packages a wheel,
# so a git-sourced install ships Python without the frontend and the gateway
# serves its "frontend not built" guidance page (dashboard/server.py resolves
# _DIST_DIR = static/dist). Upstream's CI builds the release wheel from that same
# tag, so it carries the tag's Python (checked byte-identical for the current
# pin) with the frontend staged in, and it is precisely what upstream's own image
# installs.
#
# The asset is resolved through the releases API rather than constructed from the
# tag because asset names do not always match their tag: v0.4.1 publishes
# kirocrew-0.4.1rc1-py3-none-any.whl, so a kirocrew-$V-py3-none-any.whl URL has a
# 404 waiting on a future bump. That is one unauthenticated request per build; a
# shared CI IP exhausting the anonymous 60/hour quota fails loudly here, and the
# fix would be an authenticated request via a BuildKit secret (the wheel URL is
# public, so it is not itself a secret).
#
# --no-deps on the local package is REQUIRED, not an optimisation. pyproject
# still declares kirocrew under [tool.uv.sources], so if uv resolves deps here it
# reinstalls kirocrew from the git tag and silently replaces the UI-bearing wheel
# with the UI-less build — reproduced locally, the UI vanishes from the very same
# commands.
RUN set -eux; \
    V="$(grep -oP '^kirocrew = .*tag = "v\K[^"]+' pyproject.toml)"; \
    WHEEL="$(curl -fsSL \
        "https://api.github.com/repos/kirodotdev/KiroCrew/releases/tags/v${V}" \
        | grep -o '"browser_download_url": *"[^"]*\.whl"' | head -1 | cut -d'"' -f4)"; \
    test -n "${WHEEL}"; \
    uv pip install --system --no-cache "${WHEEL}"; \
    uv pip install --system --no-cache --no-deps .; \
    python -c "import pathlib, kiro_crew; p = pathlib.Path(kiro_crew.__file__).parent / 'static' / 'dist' / 'index.html'; assert p.is_file(), f'dashboard UI missing from the install: {p}'"

# ─────────────────────────────────────────────────────────────
# Stage 3: runtime
# ─────────────────────────────────────────────────────────────
FROM python:3.14-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Everything we need — opencode + node_modules, the site-packages tree, and the
# kirocrew/gateway/uv entry points — already lives under /usr/local in the build
# stage, which shares this stage's base image. Copy it wholesale rather than
# naming site-packages by interpreter version: a hardcoded
# /usr/local/lib/python3.12 breaks the build the moment renovate bumps the base
# image to 3.14, and the version cannot be factored into an ARG without
# blinding renovate's dockerfile manager (it only rewrites lines that literally
# contain the current value, so `python:${PYTHON_VERSION}-slim` never matches).
# ponytail: costs ~the size of the base interpreter in a duplicated layer.
# Switch to installing into a fixed-path venv (/opt/venv) if image size matters.
COPY --from=kirocrew-stage /usr/local /usr/local

# Set the ACP backend to opencode (the factory reads this env var).
ENV KIROCREW_ACP_BACKEND=opencode

# Bind all interfaces INSIDE the container netns — parity with the official
# kirocrew image, which upstream's bind_address_for() docstring names as the
# reference. Without it the gateway binds loopback, so a published port or a
# kubelet probe dialing the pod IP gets connection-refused and the deployment
# never goes Ready. Exposes nothing beyond what -p / the Service publishes:
# token auth is mounted unconditionally, and only the token-exempt PROBE_PATHS
# (/api/health) answer unauthenticated.
ENV KIROCREW_BIND=0.0.0.0

# OpenCode config: auth and permissions.
# OPENCODE_AUTH_CONTENT is the JSON content of auth.json (passed at runtime).
# OPENCODE_CONFIG is the path to the opencode.json config file.
ENV OPENCODE_CONFIG=/config/opencode.json

WORKDIR /home/kirocrew/.kiro/crew/workspace

# The gateway's dashboard/API port (kiro_crew's default), not 3000.
EXPOSE 5476

# Use the patched gateway entry point (install() then kirocrew gateway).
ENTRYPOINT ["gateway"]