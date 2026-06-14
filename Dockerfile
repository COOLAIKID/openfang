# AutoEarn agent image
# Each container runs exactly one agent via:
#   python run_agent.py <AGENT_NAME>
#
# Build:
#   docker build -t autoearn .
# Run a single agent:
#   docker run -d --name ae_ceo \
#     -e AGENT_NAME=ceo \
#     -v $(pwd)/autoearn:/app:ro \
#     -v ae_data:/app/output \
#     autoearn
# Or use docker compose up -d (recommended).

FROM python:3.11-slim

# System deps (playwright Chromium, curl for health checks)
RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends \
        curl wget ca-certificates gnupg \
        libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
        libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 \
        libxrandr2 libgbm1 libasound2 libpangocairo-1.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer cache)
COPY autoearn/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the full autoearn package
COPY autoearn/ /app/

# Playwright browsers (optional — only needed if agents use browser tools)
RUN playwright install chromium --with-deps 2>/dev/null || true

# Data directories that will be bind-mounted or volume-mounted
RUN mkdir -p /app/output/articles /app/output/proposals \
             /app/output/signals  /app/output/code

# Default: run the agent named in AGENT_NAME env var
ENV AGENT_NAME=ceo
CMD ["sh", "-c", "exec python run_agent.py $AGENT_NAME"]
