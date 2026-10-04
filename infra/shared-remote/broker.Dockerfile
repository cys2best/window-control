FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi==0.115.14 uvicorn[standard]==0.34.3
COPY src/broker /app/broker
COPY src/remote_protocol.py /app/remote_protocol.py
ENV PYTHONUNBUFFERED=1
CMD ["uvicorn", "broker.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
