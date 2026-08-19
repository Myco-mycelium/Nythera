# Nyrqis Backend + Preview Server
#
# Multi-stage build for the Nyrqis Linux backend with the NUI preview server.
#
# Build:
#   docker build -t nyrqis .
#
# Run preview server:
#   docker run -p 8080:8080 nyrqis
#
# Run with custom file:
#   docker run -p 8080:8080 -v /path/to/my.nstudio:/app/my.nstudio nyrqis --file /app/my.nstudio
#
# Run backend tests:
#   docker run --rm nyrqis test
#
# Run generator validation:
#   docker run --rm nyrqis validate

FROM python:3.12-slim AS base

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install Python dependencies
COPY source/nyhal-linux-backend/requirements.txt /tmp/requirements.txt 2>/dev/null || true
RUN pip install --no-cache-dir \
    Pillow \
    pysdl2 \
    pysdl2-dll \
    pynacl \
    numpy \
    watchdog \
    pyyaml

# Copy the backend source
COPY source/nyhal-linux-backend/ /app/source/nyhal-linux-backend/

# Copy tools
COPY tools/ /app/tools/

# Copy test fixtures
COPY source/nyhal-linux-backend/tests/fixtures/ /app/source/nyhal-linux-backend/tests/fixtures/

# Copy Nyforge examples (for desktop.nstudio)
COPY --from=ghcr.io/myco-mycelium/nyforge:latest /app/examples/ /app/Nyforge/examples/ 2>/dev/null || true

# Set PYTHONPATH
ENV PYTHONPATH=/app/source/nyhal-linux-backend:/app

# Default: run the preview server
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/info')" || exit 1

# Entry point
ENTRYPOINT ["python3", "/app/tools/preview_server.py"]
CMD ["8080"]
