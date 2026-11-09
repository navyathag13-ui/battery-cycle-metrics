FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY battery_metrics battery_metrics
COPY scripts scripts
# Mount the NASA .mat files at /app/data/raw, then the container builds the database and serves it.
ENV BATTERY_DB=/app/data/battery.db
EXPOSE 8000
CMD ["sh", "-c", "python scripts/build.py > /dev/null && uvicorn battery_metrics.api:app --host 0.0.0.0 --port 8000"]
