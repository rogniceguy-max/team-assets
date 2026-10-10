FROM team-assets-web
WORKDIR /app
COPY *.py ./
COPY core ./core
COPY routers ./routers
COPY schemas ./schemas
COPY services ./services
COPY mcp-client ./mcp-client
ENV DATA_DIR=/data PYTHONUNBUFFERED=1
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8094"]
