# Multi-stage production image for the scryme backend (FastAPI).
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --user -r requirements.txt


FROM python:3.12-slim AS production

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH=/home/scryme/.local/bin:$PATH
WORKDIR /app

# pip is never invoked at runtime -- deps are installed in the builder stage and COPYed in, and
# docker-entrypoint.sh only runs alembic + uvicorn. Upgrading it isn't enough to clear image
# scans because pip vendors its own dependencies (msgpack, setuptools) under pip/_vendor, so
# remove it outright and retire the whole alert class.
RUN python -m pip uninstall -y pip

# Non-root runtime user; owns the data volume mount point.
RUN useradd --create-home --uid 1000 scryme \
    && mkdir -p /data/images \
    && chown -R scryme:scryme /data

COPY --from=builder /root/.local /home/scryme/.local
COPY --chown=scryme:scryme . .

USER scryme
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"

CMD ["sh", "docker-entrypoint.sh"]
