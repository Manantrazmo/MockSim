# ── Stage 1: Build React dashboard ───────────────────────────────────────────
FROM node:20-slim AS dashboard-builder

WORKDIR /dashboard

# Install dependencies first (layer cache)
COPY dashboard/package.json dashboard/package-lock.json* ./
# npm ci when lock file is present (CI), npm install otherwise (first-time build)
RUN if [ -f package-lock.json ]; then npm ci --prefer-offline; else npm install; fi

# Copy sources and build
COPY dashboard/ ./
RUN npm run build


# ── Stage 2: Python runtime ───────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# Build deps for asyncpg + psycopg2
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]"

# Copy application source
COPY . .

# Inject pre-built dashboard (keeps image layer clean)
COPY --from=dashboard-builder /dashboard/dist /app/dashboard/dist

CMD ["sh", "-c", "alembic upgrade head && uvicorn mocksim.main:app --host 0.0.0.0 --port 8080 --workers 1"]
