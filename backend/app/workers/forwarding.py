"""Atomically forward legacy heavy deliveries on the Redis Celery broker."""
import os
from uuid import uuid4

from celery.exceptions import Reject
from kombu import Producer
from kombu.utils.json import dumps

# No expiry: even a very late acks_late redelivery must not republish a job.
# One small receipt per forwarded delivery, stored alongside the broker data.
_FORWARD_ONCE = """
if redis.call('EXISTS', KEYS[1]) == 1 then return 0 end
redis.call('LPUSH', KEYS[2], ARGV[1])
redis.call('SET', KEYS[1], '1')
return 1
"""


def forward_heavy_task(task) -> bool:
    """Publish the original args/kwargs once, before touching DB or scratch.

    Use Celery/Kombu's normal message serialization and routing. On a private
    publication channel only, replace Redis's final LPUSH with a Lua operation
    that also records the source task id. Publication retries and redeliveries
    therefore cannot create a second ingest delivery, including a crash after
    publishing but before acknowledging the light delivery. A broker failure
    raises instead of returning success, leaving the original unacknowledged.
    """
    if os.environ.get("WORKER_ROLE") != "light":
        return False

    request = task.request
    if not request.id:
        raise RuntimeError("A heavy task on the light worker needs a delivery id")
    receipt = f"9xaipal:forward:{request.id}:{request.retries}"
    try:
        with task.app.connection_for_write() as connection:
            # This channel is not shared with a consumer or another publisher.
            channel = connection.channel()
            original_put = channel._put

            def put_once(queue, message, **_kwargs):
                priority = channel._get_message_priority(message, reverse=False)
                queue_key = channel._q_for_pri(queue, priority)
                with channel.conn_or_acquire() as client:
                    client.eval(_FORWARD_ONCE, 2, receipt, queue_key, dumps(message))

            channel._put = put_once
            try:
                task.apply_async(
                    args=request.args, kwargs=request.kwargs,
                    task_id=str(uuid4()), retries=request.retries,
                    queue="ingest", producer=Producer(channel),
                )
            finally:
                channel._put = original_put
                channel.close()
    except Exception as exc:
        # Celery normally ACKs task failures even with acks_late. Reject
        # explicitly so the original delivery survives a broker outage.
        raise Reject(str(exc), requeue=True) from exc
    return True
