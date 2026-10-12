FROM ghcr.io/kirodotdev/kirocrew:0.8.0

# renovate: datasource=npm depName=@opencode/cli
ARG OPENCODE_VERSION=2.0.26

USER root
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends awscli ca-certificates curl ffmpeg gh git nodejs npm \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g "@opencode/cli@${OPENCODE_VERSION}" \
    && opencode --version

USER kirocrew

# Placeholder, not a credential: kiro-cli whoami exits 0 when set, which satisfies KiroCrew's readiness gate.
ENV KIRO_API_KEY=placeholder-opencode-backend
