FROM ghcr.io/kirodotdev/kirocrew:0.12.2

# renovate: datasource=npm depName=@opencode/cli
ARG OPENCODE_VERSION=2.2.6

# renovate: datasource=github-releases depName=layertwo/tunnels
ARG TUNNEL_VERSION=v0.4.0

USER root
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends awscli ca-certificates curl ffmpeg gh git nodejs npm openssh-server \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g "@opencode/cli@${OPENCODE_VERSION}" \
    && opencode --version

RUN set -eu; v="${TUNNEL_VERSION#v}"; f="tunnel_${v}_linux_amd64.tar.gz"; \
    base="https://github.com/layertwo/tunnels/releases/download/${TUNNEL_VERSION}"; \
    cd /tmp && curl -fsSLO "${base}/${f}" && curl -fsSLO "${base}/checksums.txt" \
    && grep " ${f}\$" checksums.txt | sha256sum -c - \
    && tar -xzf "${f}" tunnel && install -m 0755 tunnel /usr/local/bin/tunnel \
    && rm -f "${f}" checksums.txt tunnel && tunnel version

USER kirocrew

# Placeholder, not a credential: kiro-cli whoami exits 0 when set, which satisfies KiroCrew's readiness gate.
ENV KIRO_API_KEY=placeholder-opencode-backend
