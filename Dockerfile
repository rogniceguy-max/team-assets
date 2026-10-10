FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt
COPY *.py ./
COPY core ./core
COPY routers ./routers
COPY schemas ./schemas
COPY services ./services
COPY web ./web
COPY mcp-client ./mcp-client
ENV DATA_DIR=/data PYTHONUNBUFFERED=1
EXPOSE 8000 8001
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
