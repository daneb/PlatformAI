FROM python:3.12-slim
WORKDIR /app
COPY capabilities/anomaly/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["uvicorn", "capabilities.anomaly.main:app", "--host", "0.0.0.0", "--port", "8007"]
