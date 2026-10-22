FROM python:3.14-slim
WORKDIR /srv
ENV PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY chain/GreenReceipts.artifact.json ./chain/GreenReceipts.artifact.json
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
