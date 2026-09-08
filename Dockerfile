# One image for api and worker. KiCad 10 (kicad-cli + pcbnew bindings) and Freerouting with
# its bundled JRE. Debian 13, Python 3.13.
FROM kicad/kicad:10.0

USER root
ARG FREEROUTING=2.4.1
RUN apt-get update && apt-get install -y --no-install-recommends curl unzip python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL -o /tmp/fr.zip \
       https://github.com/freerouting/freerouting/releases/download/v${FREEROUTING}/freerouting-${FREEROUTING}-linux-x64.zip \
    && unzip -q /tmp/fr.zip -d /opt && rm /tmp/fr.zip \
    && ln -s /opt/freerouting-${FREEROUTING}-linux-x64/bin/freerouting /usr/local/bin/freerouting

# venv with system site packages so `import pcbnew` resolves
RUN python3 -m venv --system-site-packages /opt/venv
ENV PATH=/opt/venv/bin:$PATH PYTHONPATH=/app/src FREEROUTING_BIN=freerouting S3_ENDPOINT=""
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY migrations ./migrations
COPY golden ./golden
RUN pip install --no-cache-dir . && mkdir -p /home/kicad/.config && chown -R kicad:kicad /home/kicad
USER kicad
