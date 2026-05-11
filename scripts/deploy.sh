#!/bin/bash
# A1 Trainer — Production Deployment Script
set -e

echo "=== A1 Trainer Production Deploy ==="

# 1. Build dashboard
echo "[1/5] Building dashboard..."
cd dashboard-ui
npm ci --production=false
npm run build
cd ..

# 2. Copy dashboard build to nginx volume
echo "[2/5] Preparing dashboard for Nginx..."
mkdir -p nginx/html
cp -r dashboard-ui/dist/* nginx/html/ 2>/dev/null || true

# 3. Run database migrations
echo "[3/5] Running database migrations..."
docker compose -f docker-compose.prod.yml run --rm backend alembic upgrade head

# 4. Start all services
echo "[4/5] Starting services..."
docker compose -f docker-compose.prod.yml up -d

# 5. Warm up local models
echo "[5/5] Warming up Ollama models..."
sleep 5
./scripts/warmup-models.sh

echo ""
echo "=== Deployment Complete ==="
echo "Dashboard: http://localhost (or your domain)"
echo "API:       http://localhost/v1/chat/completions"
echo "Admin:     http://localhost/admin/overview"
echo "Health:    http://localhost/health"
