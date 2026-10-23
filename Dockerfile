FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY app.py ./
RUN pip install --no-cache-dir '.[server]' \
    && useradd --uid 10001 --create-home dashboard \
    && mkdir -p /data \
    && chown 10001:10001 /data
ENV ANALYTICS_DATA_DIR=/data \
    PORT=8050 \
    DASH_DEBUG=false \
    PYTHONDONTWRITEBYTECODE=1
USER 10001:10001
EXPOSE 8050
VOLUME ["/data"]
# One service, two workers, one shared POSIX lock on the persistent volume.
CMD ["sh", "-c", "exec gunicorn app:server --bind 0.0.0.0:${PORT} --workers 2 --timeout 120"]
