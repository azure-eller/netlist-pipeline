# Base for the OpenHands sandbox image. OpenHands builds its agent-server on top of
# BASE_IMAGE and runs apt during that build, so the base must end as root; our product
# image ends as `kicad`. Everything else (KiCad 10, Freerouting, /opt/venv) is inherited.
# `gh` is added so the agent can open pull requests with one command.
FROM netlist-pipeline-api:latest
USER root
ARG GH=2.98.0
RUN curl -fsSL -o /tmp/gh.tgz https://github.com/cli/cli/releases/download/v${GH}/gh_${GH}_linux_amd64.tar.gz \
    && tar -xzf /tmp/gh.tgz -C /tmp && mv /tmp/gh_${GH}_linux_amd64/bin/gh /usr/local/bin/gh \
    && rm -rf /tmp/gh.tgz /tmp/gh_${GH}_linux_amd64
