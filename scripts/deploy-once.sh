#!/usr/bin/env bash
# One full deploy attempt against $DEPLOY_DIR: build the frontend, build and
# restart the containers, then poll the health check. Exits 0 only once the
# API is actually answering — non-zero otherwise, with the API's own recent
# logs printed so the failure is diagnosable from the Actions log alone.
#
# Used by .github/workflows/deploy.yml for BOTH the normal deploy and the
# automatic rollback path (deployed the current commit, then — if that
# failed its health check — restores $DEPLOY_DIR to the last known-good
# commit and calls this exact same script again). One script, one place a
# bug in the deploy sequence can hide, instead of the two paths silently
# drifting apart in the workflow YAML.
set -euo pipefail

: "${DEPLOY_DIR:?DEPLOY_DIR must be set}"

echo "Building frontend..."
docker run --rm \
  --user "$(id -u):$(id -g)" \
  -e HOME=/tmp \
  -v "$DEPLOY_DIR/frontend:/src" \
  -v "$DEPLOY_DIR/backend/frontend-dist:/dist" \
  -w /src \
  node:20-alpine \
  sh -c "npm ci --prefer-offline --no-audit --progress=false && \
         VITE_API_BASE_URL=https://9xaipal.kl1.site npm run build && \
         rm -rf /dist/* && cp -r dist/* /dist/"

echo "Building and restarting containers..."
(cd "$DEPLOY_DIR/backend" && docker compose -f docker-compose.prod.yml up -d --build)

echo "Waiting for health check..."
for i in $(seq 1 20); do
  if curl -sf http://127.0.0.1:8000/api/v1/health >/dev/null; then
    echo "Healthy after ${i} attempt(s)."
    exit 0
  fi
  sleep 3
done

echo "::error::API did not become healthy within 60s of deploy."
(cd "$DEPLOY_DIR/backend" && docker compose -f docker-compose.prod.yml logs --tail=200 api)
exit 1
