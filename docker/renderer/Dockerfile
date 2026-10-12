# The renderer is a separate, small service: it speaks HTTP to the website and
# SOAP to RCC, so a stuck render job never blocks account or upload handling.
FROM python:3.12-slim-bookworm
RUN useradd --create-home --uid 1001 renderer
WORKDIR /app
COPY renderer.py /app/renderer.py
USER renderer
ENV RENDERER_PORT=3521 PYTHONUNBUFFERED=1
EXPOSE 3521
# RCC under Wine can be busy with the startup warm-up render for a while, so the
# health check tolerates a slow first answer instead of flapping.
HEALTHCHECK --interval=15s --timeout=8s --start-period=45s --retries=10 \
    CMD ["python3", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:3521/healthz', timeout=8).status == 200 else 1)"]
ENTRYPOINT ["python3", "/app/renderer.py"]
