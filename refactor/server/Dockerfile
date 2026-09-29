FROM python:3.12-slim
WORKDIR /app
COPY refactor/server/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY refactor/server /app/refactor/server
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
CMD ["python", "-m", "refactor.server.entrypoint"]
