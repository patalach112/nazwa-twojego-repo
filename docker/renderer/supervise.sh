#!/bin/sh
# Supervise RCC and the renderer in one container.
#
# RCCService binds its SOAP port to the loopback of its own container, so a second
# container cannot reach it: the renderer has to share the container with RCC and talk
# over 127.0.0.1. This script starts the RCC launcher from docker/rcc-wine in the
# background, waits until RCC actually answers SOAP, and then becomes the renderer so
# that container shutdown reaches the renderer first and RCC is stopped by tini's
# process-group signal forwarding.
set -eu

/usr/local/bin/entrypoint.sh &
rcc_launcher=$!

ready_timeout="${RCC_READY_TIMEOUT:-300}"
deadline=$(( $(date +%s) + ready_timeout ))
until python3 /usr/local/bin/healthcheck.py > /tmp/rcc-health.log 2>&1; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
        echo "RCC did not answer SOAP within ${ready_timeout}s; starting the renderer anyway so the failure is visible" >&2
        break
    fi
    if ! kill -0 "$rcc_launcher" 2>/dev/null; then
        echo "the RCC launcher exited before RCC answered SOAP; starting the renderer anyway" >&2
        break
    fi
    sleep 5
done
echo "RCC reported: $(cat /tmp/rcc-health.log)"

# The renderer is the foreground process: it shuts down cleanly on SIGTERM, and tini
# forwards the signal to RCC in the same process group.
exec python3 /app/renderer.py
