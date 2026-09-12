#!/usr/bin/env bash
# What a deploy of $2 needs to do, given that $1 is what is running.
#
#   deploy-scope.sh <deployed-sha> <new-sha>   →   prints one of
#     full       rebuild + restart everything (also the answer when there is
#                no deployed sha to diff against, or the two can't be diffed)
#     backend    rebuild + restart the api and worker images; skip the SPA
#     frontend   rebuild the SPA only; the containers keep running untouched
#     both       backend + frontend
#     none       nothing at runtime changed: docs, tests, CI, samples. Sync
#                the files and confirm the site is still healthy, that's all
#
# Used by .github/workflows/deploy.yml to set DEPLOY_SCOPE before calling
# deploy-once.sh. Without this, every merge — a README fix included — ran
# `npm ci`, a Vite build, `docker compose up -d --build` and an API
# container recreate: five and a half minutes and a short API outage to
# ship a paragraph of Markdown. CI already skips the backend and frontend
# jobs when their trees are untouched (ci.yml's "detect changes"); this is
# the deploy-side twin of that, and it must agree with it on what "the
# backend" and "the frontend" are.
#
# ⚠ What counts as backend is everything the api/worker IMAGES or their
# runtime config are built from, not only backend/app: pyproject + uv.lock
# (dependencies), the Dockerfiles and docker/ (the image), the compose file
# (env, mounts, limits), and this very script pair (a change to how we
# deploy should be exercised by a real deploy, not by the next code change
# that happens along). backend/nginx is NOT included: the host's nginx
# config is installed by hand with sudo and nothing here can reload it — a
# change there is announced as a notice by the workflow instead.
set -euo pipefail

deployed="${1:-}"
new="${2:?new sha required}"

if [ -z "$deployed" ] || ! git cat-file -e "$deployed^{commit}" 2>/dev/null; then
  echo full
  exit 0
fi

changed=$(git diff --name-only "$deployed" "$new") || { echo full; exit 0; }

backend=0
frontend=0
while IFS= read -r path; do
  [ -z "$path" ] && continue
  case "$path" in
    backend/nginx/*) ;;                                  # hand-installed, see above
    backend/tests/*|backend/pytest.ini|backend/*.md|backend/README*) ;;  # never reaches a container
    backend/*|scripts/deploy-once.sh|scripts/deploy-scope.sh) backend=1 ;;
    frontend/*) frontend=1 ;;
    *) ;;                                                # docs, CI, samples, root files
  esac
done <<< "$changed"

if [ "$backend" = 1 ] && [ "$frontend" = 1 ]; then echo both
elif [ "$backend" = 1 ]; then echo backend
elif [ "$frontend" = 1 ]; then echo frontend
else echo none
fi
