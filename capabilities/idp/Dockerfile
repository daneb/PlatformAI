FROM python:3.12-slim

WORKDIR /app
COPY capabilities/idp/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "capabilities.idp.main:app", "--host", "0.0.0.0", "--port", "8005"]
