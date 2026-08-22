# Dockerfile for KiroCrew with the OpenCode ACP backend (runtime patch approach).
#
# No source patching — KiroCrew is pip-installed from PyPI, and the
# opencode_provider package monkey-patches it at runtime before the
# gateway boots. Inspired by lenovo1996/KiroCrew-OpenAI-Compatible.
#
# Build:
#   docker build -t kirocrew-opencode .
#
# Run:
#   docker run -e KIROCREW_ACP_BACKEND=opencode \
#     -e OPENCODE_AUTH_CONTENT='...' \
#     -p 3000:3000 kirocrew-opencode

# ─────────────────────────────────────────────────────────────
# Stage 1: install opencode (Node CLI)
# ─────────────────────────────────────────────────────────────
FROM node:24-slim AS opencode-stage

RUN npm install -g opencode@1.18.18

# ─────────────────────────────────────────────────────────────
# Stage 2: install KiroCrew + opencode_provider
# ─────────────────────────────────────────────────────────────
FROM python:3.12-slim AS kirocrew-stage

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy opencode binary from stage 1.
COPY --from=opencode-stage /usr/local/lib/node_modules /usr/local/lib/node_modules
COPY --from=opencode-stage /usr/local/bin/opencode /usr/local/bin/opencode

# Install KiroCrew from PyPI (no source patching needed).
# Pinned to 0.3.0 — the membership-set architecture makes patching cleaner.
ARG KIROCREW_VERSION=0.3.0
RUN pip install --no-cache-dir kirocrew==${KIROCREW_VERSION}

# Copy the opencode_provider package + gateway entry point.
COPY opencode_provider/ /opt/opencode_provider/opencode_provider/
COPY gateway.py /opt/opencode_provider/gateway.py

RUN pip install --no-cache-dir -e /opt/opencode_provider

# ─────────────────────────────────────────────────────────────
# Stage 3: runtime
# ─────────────────────────────────────────────────────────────
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy opencode from build stage.
COPY --from=kirocrew-stage /usr/local/lib/node_modules /usr/local/lib/node_modules
COPY --from=kirocrew-stage /usr/local/bin/opencode /usr/local/bin/opencode

# Copy installed Python packages.
COPY --from=kirocrew-stage /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=kirocrew-stage /usr/local/bin/kirocrew /usr/local/bin/kirocrew
COPY --from=kirocrew-stage /usr/local/bin/gateway /usr/local/bin/gateway

# Set the ACP backend to opencode (the factory reads this env var).
ENV KIROCREW_ACP_BACKEND=opencode

# OpenCode config: auth and permissions.
# OPENCODE_AUTH_CONTENT is the JSON content of auth.json (passed at runtime).
# OPENCODE_CONFIG is the path to the opencode.json config file.
ENV OPENCODE_CONFIG=/config/opencode.json

WORKDIR /home/kirocrew/.kiro/crew/workspace

EXPOSE 3000

# Use the patched gateway entry point (install() then kirocrew gateway).
ENTRYPOINT ["gateway"]