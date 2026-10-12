FROM debian:bookworm-slim

RUN dpkg --add-architecture i386 \
    && apt-get update \
    && apt-get install --no-install-recommends -y \
        wine wine32:i386 wine64 \
        libgl1-mesa-dri:i386 libgl1:i386 libglu1-mesa:i386 \
        xvfb xauth tini python3 ca-certificates fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 rcc

ENV WINEARCH=win32 \
    WINEPREFIX=/home/rcc/prefix \
    WINEDEBUG=-all \
    WINEDLLOVERRIDES="opengl32=b;glu32=b;winemenubuilder.exe=d" \
    LIBGL_ALWAYS_SOFTWARE=1 \
    RCC_PORT=64989 \
    RCC_EXE=RCCService.exe

COPY entrypoint.sh run-wine.sh healthcheck.py lua_probe.py json_probe.py json_probe.lua \
    thumbnail_probe.py thumbnail_probe.lua /usr/local/bin/
RUN chmod 755 /usr/local/bin/entrypoint.sh /usr/local/bin/run-wine.sh \
    && mkdir /opt/rcc \
    && chown rcc:rcc /opt/rcc

USER rcc
WORKDIR /home/rcc
EXPOSE 64989/tcp
HEALTHCHECK --interval=10s --timeout=5s --start-period=90s --retries=3 \
    CMD ["python3", "/usr/local/bin/healthcheck.py"]
ENTRYPOINT ["/usr/bin/tini", "-g", "--", "/usr/local/bin/entrypoint.sh"]
