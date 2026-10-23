# CareCircle MCP server as a container (AWS App Runner, ECS/Fargate or any container host).
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY carecircle ./carecircle
# public mode: only /mcp, /healthz and /.well-known/* are served; set CARECIRCLE_TOKEN, CARECIRCLE_PUBLIC_URL and
# CARECIRCLE_ALLOWED_HOSTS at deploy time
ENV CARECIRCLE_HOST=0.0.0.0 CARECIRCLE_PORT=8080 CARECIRCLE_DB=/data/carecircle.db CARECIRCLE_DEMO=0 PYTHONUNBUFFERED=1
RUN mkdir -p /data
EXPOSE 8080
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz')"
CMD ["python", "-m", "carecircle"]
