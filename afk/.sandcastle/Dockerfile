# AFK implement sandbox (write/git). Separate from sql-buddy-review image.
# Build: npm run sandbox:build  →  sql-buddy-afk:pi (or AFK_DOCKER_IMAGE)
FROM node:22-bookworm

RUN apt-get update && apt-get install -y \
  git \
  curl \
  jq \
  ca-certificates \
  ripgrep \
  && rm -rf /var/lib/apt/lists/*

ARG AGENT_UID=1000
ARG AGENT_GID=1000

RUN groupmod -o -g $AGENT_GID node \
  && usermod -o -u $AGENT_UID -g $AGENT_GID -d /home/agent -m -l agent node

RUN npm install -g @earendil-works/pi-coding-agent

# Pi 0.84+: provider zai-coding-cn + env ZAI_CODING_CN_API_KEY (no models.json apiKey).
USER ${AGENT_UID}:${AGENT_GID}
WORKDIR /home/agent
RUN mkdir -p /home/agent/.pi/agent \
  && git config --global user.name "sql-buddy-afk" \
  && git config --global user.email "sql-buddy-afk@local" \
  && git config --global --add safe.directory '*'

ENTRYPOINT ["sleep", "infinity"]
