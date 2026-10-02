#!/usr/bin/env bash
# Run from the NEW deployment tree before restoring a pre-split revision.
# Quiesce publishers and consumers first, then return ingest work to celery.
set -euo pipefail

# Stop autoheal first so it cannot restart workers during queue recovery.
if docker inspect 9xaipal-autoheal >/dev/null 2>&1; then
  docker stop 9xaipal-autoheal
fi
containers=()
for container in 9xaipal-api 9xaipal-celery-worker 9xaipal-celery-worker-light; do
  if docker inspect "$container" >/dev/null 2>&1; then
    containers+=("$container")
  fi
done
if ((${#containers[@]})); then
  docker stop "${containers[@]}"
fi
# This also works when the old Compose file no longer defines the service.
if docker inspect 9xaipal-celery-worker-light >/dev/null 2>&1; then
  docker rm -f 9xaipal-celery-worker-light
fi

# Each RPOPLPUSH is atomic and preserves the exact serialized queued payload.
# Redis queues use LPUSH/RPOP (oldest on the right). Append migrated work after
# the existing default backlog; retain FIFO within each priority bucket.
# Recover ingest's reserved deliveries too: a killed worker may not have
# restored them on shutdown. Leave light/default unacked entries for the old
# worker's normal startup recovery. The full old deploy restarts autoheal/API.
prefix="${CELERY_QUEUE_PREFIX:-}"
lua=$(cat <<'LUA'
local prefix = ARGV[1]
local moved = 0
for _, suffix in ipairs({'', '\006\0223', '\006\0226', '\006\0229'}) do
  while redis.call('RPOPLPUSH', prefix .. 'ingest' .. suffix, prefix .. 'celery' .. suffix) do
    moved = moved + 1
  end
end
local unacked = prefix .. 'unacked'
local index = prefix .. 'unacked_index'
-- The sorted index retains reservation order. All workers are stopped.
for _, tag in ipairs(redis.call('ZRANGE', index, 0, -1)) do
  local raw = redis.call('HGET', unacked, tag)
  if raw then
    local delivery = cjson.decode(raw)
    if delivery[3] == 'ingest' then
      local message = delivery[1]
      local priority = tonumber(message.properties.priority) or 0
      local suffix = ''
      if priority >= 9 then suffix = '\006\0229'
      elseif priority >= 6 then suffix = '\006\0226'
      elseif priority >= 3 then suffix = '\006\0223' end
      redis.call('LPUSH', prefix .. 'celery' .. suffix, cjson.encode(message))
      redis.call('HDEL', unacked, tag)
      redis.call('ZREM', index, tag)
      moved = moved + 1
    end
  end
end
return moved
LUA
)
moved=$(docker exec 9xaipal-redis redis-cli --raw EVAL "$lua" 0 "$prefix")
# redis-cli can print an error reply and still exit zero. Do not let rollback
# proceed to the old worker unless Redis returned the integer move count.
if [[ ! "$moved" =~ ^[0-9]+$ ]]; then
  printf 'Redis queue recovery failed: %s\n' "$moved" >&2
  exit 1
fi
printf 'Returned %s ingest delivery(s) to celery\n' "$moved"
