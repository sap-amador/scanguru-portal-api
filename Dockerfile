FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

COPY . /app

RUN useradd -m appuser && chown -R appuser /app
USER appuser

EXPOSE 8000

# Migrations run before the web server binds.
#
# Without this the container started gunicorn directly, so a deploy carrying a
# new column came up green and then 500'd on every read — SQLAlchemy selects
# every mapped column, so a column present in the model and absent from the
# database breaks queries, not just writes.
#
# Shell form so `&&` is interpreted; `exec` so gunicorn replaces the shell as
# PID 1 and keeps receiving Railway's stop signals. A failed migration now
# blocks boot and shows as a failed deploy, which is the failure mode you want
# over a healthy-looking service returning errors.
CMD alembic upgrade head && \
    exec gunicorn -k uvicorn.workers.UvicornWorker -w 2 --timeout 300 -b 0.0.0.0:8000 app.main:app
