# syntax=docker/dockerfile:1.7

ARG NODE_VERSION=22.20.0
ARG PYTHON_VERSION=3.12.10
ARG NGINX_VERSION=1.28.0

FROM node:${NODE_VERSION}-bookworm-slim AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --ignore-scripts
COPY frontend/ ./
RUN npm run build

FROM python:${PYTHON_VERSION}-slim-bookworm AS backend-wheels
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /build
COPY backend/requirements.txt ./requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip python -m pip wheel --wheel-dir /wheels -r requirements.txt

FROM python:${PYTHON_VERSION}-slim-bookworm AS document-worker-wheels
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /build
COPY backend/requirements.txt backend/requirements-documents.txt ./
RUN --mount=type=cache,target=/root/.cache/pip python -m pip wheel --wheel-dir /wheels -r requirements.txt -r requirements-documents.txt

FROM python:${PYTHON_VERSION}-slim-bookworm AS backend-base
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown
ARG APP_VERSION=0.0.0
LABEL org.opencontainers.image.vendor="MentorFlow" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.source="https://github.com/tonyswift868/AI-Knowledge-visualization"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/backend \
    MENTORFLOW_DB=/var/lib/mentorflow/app.db \
    MENTORFLOW_ORM_DB=/var/lib/mentorflow/mentorflow_v2.db \
    MENTORFLOW_ALLOWED_ORIGINS=https://invalid.example \
    MENTORFLOW_PROVIDER_MODE=fake
RUN groupadd --system --gid 10001 mentorflow \
    && useradd --system --uid 10001 --gid mentorflow --home-dir /app mentorflow \
    && mkdir -p /app /var/lib/mentorflow /run/mentorflow \
    && chown -R mentorflow:mentorflow /app /var/lib/mentorflow /run/mentorflow
WORKDIR /app
COPY --from=backend-wheels /wheels /wheels
COPY backend/requirements.txt /app/backend/requirements.txt
RUN python -m pip install --no-index --find-links=/wheels -r /app/backend/requirements.txt && rm -rf /wheels
COPY --chown=mentorflow:mentorflow backend/ /app/backend/
COPY --chown=mentorflow:mentorflow content/ /app/content/
COPY --chown=mentorflow:mentorflow story_packs/ /app/story_packs/
USER 10001:10001
VOLUME ["/var/lib/mentorflow"]

FROM backend-base AS backend-image
LABEL org.opencontainers.image.title="MentorFlow backend"
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).read()"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--app-dir", "/app/backend", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=127.0.0.1"]

FROM backend-base AS document-worker-image
USER root
COPY --from=document-worker-wheels /wheels /wheels
COPY backend/requirements-documents.txt /app/backend/requirements-documents.txt
RUN python -m pip install --no-index --find-links=/wheels -r /app/backend/requirements-documents.txt && rm -rf /wheels
USER 10001:10001
LABEL org.opencontainers.image.title="MentorFlow document worker"
CMD ["python", "-X", "utf8", "-m", "app.sources.document.ingestion.worker", "--serve", "--config", "/run/mentorflow/document-worker-config.json"]

FROM nginx:${NGINX_VERSION}-alpine AS frontend-image
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown
ARG APP_VERSION=0.0.0
LABEL org.opencontainers.image.title="MentorFlow frontend" \
      org.opencontainers.image.vendor="MentorFlow" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.source="https://github.com/tonyswift868/AI-Knowledge-visualization"
COPY deploy/enterprise/nginx.conf /etc/nginx/nginx.conf
COPY --from=frontend-build /build/frontend/dist /usr/share/nginx/html
RUN chown -R nginx:nginx /usr/share/nginx/html
USER 101:101
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/api/health"]
CMD ["nginx", "-g", "daemon off;"]
