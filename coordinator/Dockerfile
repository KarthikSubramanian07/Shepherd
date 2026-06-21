# Shepherd Coordinator — slim relay-only image.
#
# Runs ONLY the coordinator relay (coordinator/server.py). Does NOT require
# Agent S, Playwright, pyautogui, GUI deps, or any of the heavy ML/vision
# packages. Works on any container host: a VPS with Docker, Railway, Render,
# Fly.io, or even a Raspberry Pi.
#
# Build:  docker build -f coordinator/Dockerfile -t shepherd-coordinator .
# Run:    docker run -p 8770:8770 -e COORDINATOR_TOKEN=secret shepherd-coordinator

FROM python:3.12-slim

WORKDIR /app

# Install only the coordinator's runtime deps (no GUI, no ML, no browser).
RUN pip install --no-cache-dir \
    "fastapi>=0.115.0" \
    "uvicorn[standard]>=0.29.0" \
    "pydantic-settings>=2.2.0" \
    "python-dotenv>=1.2.2"

# Copy only the files the coordinator needs at runtime.
COPY compat.py /app/compat.py
COPY config.py /app/config.py
COPY coordinator/ /app/coordinator/

# The coordinator optionally imports dashboard.deepgram_routes; create an empty
# package so the import doesn't fail (the try/except in server.py handles it).
RUN mkdir -p /app/dashboard && touch /app/dashboard/__init__.py

# COORDINATOR_TOKEN is set at runtime (docker run -e), not baked into the image.
ENV COORDINATOR_PORT=8770
ENV RELAY_FPS=3.0
ENV RELAY_FRAME_WIDTH=1024
ENV RELAY_FRAME_QUALITY=55

EXPOSE 8770

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8770/api/health')"

CMD ["python", "-m", "coordinator"]
