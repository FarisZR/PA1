FROM python:3.13-slim@sha256:7ce4b6dfe35e55397b7cda544f8a13f191b7ae28dc5aad71fe664dbc9bc2623f

RUN pip install --no-cache-dir 'litellm[proxy]==1.83.0'

WORKDIR /app
COPY config.yaml /app/config.yaml

EXPOSE 4000
CMD ["litellm", "--config", "/app/config.yaml", "--host", "0.0.0.0", "--port", "4000", "--telemetry", "False"]
