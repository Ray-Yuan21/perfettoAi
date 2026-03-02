# Stage 1: Build frontend
FROM node:20-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ .
RUN npm run build

# Stage 2: Backend + embedded frontend
FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
COPY backend/pyproject.toml backend/
COPY backend/perfetto_trace_analyzer/ backend/perfetto_trace_analyzer/
COPY backend/run_server.py backend/
COPY backend/config.yaml.example backend/
RUN pip install --no-cache-dir -e backend/
# Pre-download trace_processor binary (avoids runtime download failures)
RUN mkdir -p /app/bin && \
    python -c "from perfetto.trace_processor.platform import PlatformDelegate; PlatformDelegate().get_shell_path(None)" && \
    python /tmp/trace_processor_python_api --version && \
    cp ~/.local/share/perfetto/prebuilts/trace_processor_shell /app/bin/trace_processor && \
    chmod +x /app/bin/trace_processor
ENV TRACE_PROCESSOR_SHELL_PATH=/app/bin/trace_processor
COPY --from=frontend-build /frontend/dist backend/static/
EXPOSE 8000
WORKDIR /app/backend
CMD ["python", "run_server.py"]
