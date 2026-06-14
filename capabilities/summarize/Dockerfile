FROM python:3.12-slim

WORKDIR /app
COPY capabilities/summarize/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "capabilities.summarize.main:app", "--host", "0.0.0.0", "--port", "8001"]
