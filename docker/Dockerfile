# One image per study repo: the repo at a pinned commit, its dependencies, and a
# pinned Claude Code. Each trial starts a fresh, disposable container from it.
ARG PY_VERSION=3.12
FROM python:${PY_VERSION}-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates bash iptables dnsutils procps build-essential \
    && rm -rf /var/lib/apt/lists/*

# The agent and the tests run as this unprivileged user. (Claude Code refuses to
# skip permission prompts as root, and least privilege is right anyway.)
RUN useradd --create-home --shell /bin/bash agent

# The repo, with enough history to check out every task's base commit.
WORKDIR /work
ARG REPO_URL
ARG REPO_SHA
ARG CLONE_SINCE
RUN git clone --quiet --shallow-since="${CLONE_SINCE}" "${REPO_URL}" repo \
 && git -C repo checkout --quiet "${REPO_SHA}"

# Dependencies are installed at build time, so trials need no network for them.
WORKDIR /work/repo
ARG INSTALL_CMD
RUN python -m pip install --no-cache-dir --upgrade pip \
 && bash -c "${INSTALL_CMD}"
RUN chown -R agent:agent /work

# Claude Code, pinned (see study.toml). Native installer, no Node needed.
USER agent
ARG CLAUDE_CODE_VERSION=stable
RUN curl -fsSL https://claude.ai/install.sh | bash -s "${CLAUDE_CODE_VERSION}"
ENV PATH="/home/agent/.local/bin:${PATH}"
RUN claude --version > /home/agent/claude-version.txt

# Back to root only so the entrypoint can install the egress firewall.
USER root
RUN git config --system --add safe.directory '*'
COPY init-firewall.sh entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/init-firewall.sh /usr/local/bin/entrypoint.sh
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["sleep", "infinity"]
