# The renderer, with RCC inside the same container.
#
# RCCService binds its SOAP port to the loopback of its own container, so a second
# container cannot reach it (the standalone experiment only notices because it talks to
# RCC with `docker exec`). The renderer therefore shares the container with RCC and
# talks to 127.0.0.1:64989; per-container isolation is unchanged because nothing is
# published except the renderer's own port.
#
# The Wine/Xvfb package list is the one in docker/rcc-wine/Dockerfile, and
# scripts/test_renderer_contract.py fails when the two lists drift apart. The RCC
# launcher, its PE check, its AppSettings rewrite and its script verification are the
# very same files from docker/rcc-wine, copied here instead of duplicated.
#
# Build context is docker/ so both directories are reachable.
FROM debian:bookworm-slim

RUN dpkg --add-architecture i386 \
    && apt-get update \
    && apt-get install --no-install-recommends -y \
        wine wine32:i386 wine64 \
        libgl1-mesa-dri:i386 libgl1:i386 libglu1-mesa:i386 \
        xvfb xauth tini python3 ca-certificates openssl fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 rcc

ENV WINEARCH=win32 \
    WINEPREFIX=/home/rcc/prefix \
    WINEDEBUG=-all \
    WINEDLLOVERRIDES="opengl32=b;glu32=b;winemenubuilder.exe=d" \
    LIBGL_ALWAYS_SOFTWARE=1 \
    RCC_PORT=64989 \
    RCC_EXE=RCCService.exe \
    RCC_SOAP_NAMESPACE=http://silrev.biz/ \
    RCC_SOAP_URL=http://127.0.0.1:64989/ \
    RENDERER_PORT=3521 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY rcc-wine/entrypoint.sh rcc-wine/run-wine.sh rcc-wine/healthcheck.py \
    rcc-wine/required-thumbnail-scripts.txt /usr/local/bin/
COPY renderer/renderer.py renderer/asset_tls.py /app/
COPY renderer/supervise.sh /usr/local/bin/renderer-supervise.sh
RUN chmod 755 /usr/local/bin/entrypoint.sh /usr/local/bin/run-wine.sh /usr/local/bin/renderer-supervise.sh \
    && mkdir -p /opt/rcc /app \
    && chown rcc:rcc /opt/rcc /app

USER rcc
WORKDIR /home/rcc
EXPOSE 3521/tcp
# RCC under Wine can take a while on a cold prefix; the renderer only listens once it
# is up, and RCC itself is covered by its own SOAP check inside supervise.sh.
HEALTHCHECK --interval=15s --timeout=8s --start-period=150s --retries=12 \
    CMD ["python3", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:3521/healthz', timeout=8).status == 200 else 1)"]
ENTRYPOINT ["/usr/bin/tini", "-g", "--", "/usr/local/bin/renderer-supervise.sh"]
