# Spec section 8: pin the Python minor version so a rebuild months from now
# gets the same interpreter.
FROM python:3.12-slim

# Keeps logs unbuffered so docker logs shows them as they happen, and stops
# .pyc files being written into the image layer.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /srv

# Requirements first: this layer is cached until the pins actually change.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY migrations/ ./migrations/
COPY web/ ./web/

EXPOSE 8080

# Spec 6.1: one worker. Several processes writing one SQLite file produce
# intermittent "database is locked" errors.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
