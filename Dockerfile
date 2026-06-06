# Single image used by both docker-compose services (refresh + dashboard);
# they differ only by the command they run.
FROM python:3.12-slim

WORKDIR /app

# DuckDB lives on a shared volume so the refresh job (writer) and the dashboard
# (read-only) see the same database.
ENV DUCKDB_PATH=/data/jira.duckdb

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Default command is overridden per-service in docker-compose.yml.
CMD ["python", "jira_agile_pipeline.py"]
