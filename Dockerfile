# Review agent image — pi (coding agent) in a minimal container.
# Build once on web01:  docker build -t sql-buddy-review -f Dockerfile .
#
# The image holds ONLY the runtime (pi + node + git + ripgrep). All review
# logic (rubric, skill, output contract) lives in the repo and is read by pi
# from the mounted /workspace at runtime — so editing AGENTS.md or the skill
# never requires a rebuild. See docs/adr/0001-*.md for the security rationale.
#
# Based on pi's reference Dockerfile.pi (packages/coding-agent/docs/containerization.md).

FROM node:24-bookworm-slim

RUN apt-get update \
  && apt-get install -y --no-install-recommends bash ca-certificates git ripgrep \
  && rm -rf /var/lib/apt/lists/*
RUN npm install -g --ignore-scripts @earendil-works/pi-coding-agent

WORKDIR /workspace
ENTRYPOINT ["pi"]
