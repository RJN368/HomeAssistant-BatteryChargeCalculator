FROM python:3.12-slim

WORKDIR /app

# Install build deps for cryptography on slim image
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev libssl-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY entrypoint.py .

# /data is a named volume: holds api_key, certs/, model.pkl
VOLUME ["/data"]

EXPOSE 8765

CMD ["python", "entrypoint.py"]
