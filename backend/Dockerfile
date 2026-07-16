FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

# 3 workers fits the 2 vCPU / 2GB droplet: leaves headroom for the worker
# container + Caddy + web while letting one slow CPU-bound scan keep handling
# concurrent requests on the other workers.
# Access log re-enabled so we can see request/response status + timing in
# production — pair with the per-stage scan timing in services.py.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "3", "--log-level", "info"]
