# SAMOURAIS SCRAPPER — Python/Flask
FROM python:3.12-slim

# FFmpeg + Chromium deps for scrapling/patchright headless browser
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg fonts-dejavu-core fonts-liberation curl \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libdbus-1-3 libxkbcommon0 \
    libatspi2.0-0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m patchright install chromium

# Copy app source
COPY . .

# Default data directory — overridden by DATA_DIR env var when a Railway
# volume is mounted (e.g. DATA_DIR=/data with volume at /data).
# The app creates subdirs at startup via ensure_data_dirs().
ENV DATA_DIR=/data

# Railway injects PORT env var; fallback 8080
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8080}/ || exit 1

CMD ["python", "run.py"]
