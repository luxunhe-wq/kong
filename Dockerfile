FROM docker:28-cli AS docker_cli

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KONG_HOST=0.0.0.0 \
    KONG_PORT=8080 \
    KONG_DATA_DIR=/var/lib/kong

WORKDIR /app
COPY --from=docker_cli /usr/local/bin/docker /usr/local/bin/docker
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py auth.py storage.py ports.py host.py ./
COPY static ./static
COPY deploy/healthcheck.py ./healthcheck.py

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "/app/healthcheck.py"]
CMD ["python", "server.py"]
