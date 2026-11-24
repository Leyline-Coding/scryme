#!/bin/sh
# Production entrypoint: apply migrations, then serve.
set -e

echo "Applying database migrations..."
alembic upgrade head

# Which peers may tell us the real client address (X-Forwarded-For / X-Forwarded-Proto).
#
# The app rate-limits per client address. Behind a reverse proxy the TCP peer is always the proxy,
# so unless the proxy's forwarded headers are trusted every visitor shares one bucket. Trusting
# them from the wrong peer is worse, though: anyone who can reach this port directly could then
# claim any address. So the default trusts loopback only -- right for `docker run -p 8000:8000`
# with no proxy -- and docker-compose.yml widens it, because there the port is not published and
# the only peer is the bundled nginx. Accepts a comma-separated list of IPs/CIDRs, or `*`.
FORWARDED_TRUST="${SCRYME_FORWARDED_ALLOW_IPS:-${FORWARDED_ALLOW_IPS:-127.0.0.1}}"

echo "Starting scryme..."
exec uvicorn src.main:app --host 0.0.0.0 --port 8000 \
    --proxy-headers --forwarded-allow-ips "$FORWARDED_TRUST"
