# =====================================================================
# Containerfile - Classroom Attendance Tracker
# Build : podman build -t attendance-tracker:2.0 -f Containerfile .
# Run   : podman run -d --name attendance-app -p 5000:5000 \
#           -v attendance-data:/app/data attendance-tracker:2.0
# =====================================================================

# Fully-qualified image name avoids Podman's "short-name resolution" prompt/error
FROM docker.io/library/python:3.12-slim

LABEL org.opencontainers.image.title="Classroom Attendance Tracker" \
      org.opencontainers.image.description="Flask + SQLite attendance tracker (21CSE216P capstone)" \
      org.opencontainers.image.version="2.0"

# PYTHONDONTWRITEBYTECODE : no .pyc files in the image
# PYTHONUNBUFFERED        : logs appear immediately in `podman logs`
# DATA_DIR                : where database.py stores attendance.db
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data \
    PORT=5000

WORKDIR /app

# Install dependencies first so this layer is cached between code changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application source (see .containerignore for excluded files)
COPY app.py database.py healthcheck.py ./
COPY templates/ templates/
COPY static/ static/

# Persistent data directory - mount a Podman volume here
RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 5000

# One worker + threads: SQLite works best with a single writer process
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", \
     "--access-logfile", "-", "--error-logfile", "-", "app:app"]
