#!/usr/bin/env bash
# One full deploy attempt against $DEPLOY_DIR: build what changed, restart
# what needs it, then poll the health check. Exits 0 only once the API is
# actually answering — non-zero otherwise, with the API's own recent logs
# printed so the failure is diagnosable from the Actions log alone.
#
# Used by .github/workflows/deploy.yml for BOTH the normal deploy and the
# automatic rollback path (deployed the current commit, then — if that
# failed its health check — restores $DEPLOY_DIR to the last known-good
# commit and calls this exact same script again). One script, one place a
# bug in the deploy sequence can hide, instead of the two paths silently
# drifting apart in the workflow YAML.
#
# DEPLOY_SCOPE (default: full) says how much of that to do — see
# scripts/deploy-scope.sh, which computes it from the diff between the
# running commit and the new one:
#   full      SPA build + `compose up -d --build` of every service
#   both      SPA build + rebuild/restart of api and celery_worker only
#   backend   rebuild/restart of api and celery_worker; the SPA is untouched
#   frontend  SPA build only; no container is rebuilt or restarted
#   none      nothing built, nothing restarted — the files were synced and
#             the health check confirms the site is still up
# The health check runs in every scope: it is what "deployed" means here.
set -euo pipefail

: "${DEPLOY_DIR:?DEPLOY_DIR must be set}"
SCOPE="${DEPLOY_SCOPE:-full}"
echo "Deploy scope: $SCOPE"

build_frontend() {
  echo "Building frontend..."
  # `npm ci` is the slow half of a frontend build (~a minute of deleting and
  # reinstalling node_modules) and only ever changes anything when
  # package-lock.json did. The lockfile's hash is recorded next to the
  # install it produced; a matching hash skips the install and goes
  # straight to the Vite build. node_modules survives between deploys
  # because deploy.yml's rsync excludes it.
  docker run --rm \
    --user "$(id -u):$(id -g)" \
    -e HOME=/tmp \
    -v "$DEPLOY_DIR/frontend:/src" \
    -v "$DEPLOY_DIR/backend/frontend-dist:/dist" \
    -w /src \
    node:20-alpine \
    sh -c '
      set -e
      lock_hash=$(sha256sum package-lock.json | cut -c1-64)
      if [ -d node_modules ] && [ "$(cat node_modules/.deployed-lock-hash 2>/dev/null)" = "$lock_hash" ]; then
        echo "package-lock.json unchanged — skipping npm ci"
      else
        npm ci --prefer-offline --no-audit --progress=false
        echo "$lock_hash" > node_modules/.deployed-lock-hash
      fi
      VITE_API_BASE_URL=https://9xaipal.kl1.site npm run build
      rm -rf /dist/* && cp -r dist/* /dist/'
}

case "$SCOPE" in
  full|both|frontend) build_frontend ;;
esac

case "$SCOPE" in
  full)
    echo "Building and restarting every container..."
    (cd "$DEPLOY_DIR/backend" && docker compose -f docker-compose.prod.yml up -d --build)
    ;;
  both|backend)
    # Only the two services built from this repo. postgres, redis and
    # autoheal are pinned images with nothing to rebuild, and `up -d` on
    # them would still be a no-op — but naming the services keeps the
    # intent explicit and the output short.
    echo "Rebuilding and restarting api + celery_worker..."
    (cd "$DEPLOY_DIR/backend" && docker compose -f docker-compose.prod.yml up -d --build api celery_worker)
    ;;
  frontend|none)
    echo "No container rebuilt or restarted (scope: $SCOPE)."
    ;;
  *)
    echo "::error::Unknown DEPLOY_SCOPE '$SCOPE' (expected full|both|backend|frontend|none)."
    exit 2
    ;;
esac

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
