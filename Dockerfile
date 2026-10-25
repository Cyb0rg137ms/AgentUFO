# CPU-only image for the local AgentUFO server. No model provider is bundled:
# point it at Ollama (OLLAMA_HOST) or set a cloud API key at runtime.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md LICENSE run_web_ui.py ./
COPY agent_core ./agent_core
COPY server ./server
COPY web ./web
RUN pip install --no-cache-dir . && useradd -m ufo
USER ufo
WORKDIR /home/ufo/workspace
EXPOSE 8000
# 0.0.0.0 is needed inside the container; publish only to loopback on the host:
#   docker run --rm -p 127.0.0.1:8000:8000 agentufo
CMD ["agentufo", "--host", "0.0.0.0", "--port", "8000", "--no-browser"]
