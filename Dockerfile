FROM ghcr.io/kirodotdev/kirocrew:0.7.2

# renovate: datasource=github-releases depName=anomalyco/opencode
ARG OPENCODE_VERSION=1.18.30

USER root
RUN curl -fsSL "https://github.com/anomalyco/opencode/releases/download/v${OPENCODE_VERSION}/opencode-linux-x64.tar.gz" \
    | tar -xz -C /usr/local/bin opencode \
    && opencode --version
USER kirocrew

# Placeholder, not a credential: kiro-cli whoami exits 0 when set, which satisfies KiroCrew's readiness gate.
ENV KIRO_API_KEY=placeholder-opencode-backend
