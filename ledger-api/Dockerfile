FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts

EXPOSE 8100

# Same image runs two roles, same pattern as backend/Dockerfile's api/worker
# split (see docker-compose.prod.yml):
#   api:    uvicorn app.main:app       (this default CMD)
#   worker: python -m app.worker       (override `command:` in compose)
#
# The production circlejerks database is mounted READ-ONLY into this
# container (see app/db.py) at LEDGER_PRODUCTION_DATABASE_PATH; this
# service's own database (LEDGER_LEDGER_DATABASE_PATH) is a separate volume
# this container has read-write access to.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100", "--workers", "2", "--log-level", "info"]
