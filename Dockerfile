FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SITEQ_HOST=0.0.0.0 \
    SITEQ_PORT=8765 \
    SITEQ_DATABASE=/data/siteq.sqlite3

WORKDIR /app
RUN groupadd --system siteq && useradd --system --gid siteq --home-dir /app siteq \
    && mkdir -p /data \
    && chown siteq:siteq /data

COPY --chown=siteq:siteq index.html ./index.html
COPY --chown=siteq:siteq backend ./backend

USER siteq
EXPOSE 8765
CMD ["python3", "backend/server.py"]
