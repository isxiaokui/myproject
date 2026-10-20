FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --default-timeout=120 --no-cache-dir -r requirements.txt

COPY app ./app
COPY data/flowers.txt ./data/flowers.txt
COPY scripts ./scripts

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data/chroma_db \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 3001

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3001", "--proxy-headers", "--forwarded-allow-ips=*"]
