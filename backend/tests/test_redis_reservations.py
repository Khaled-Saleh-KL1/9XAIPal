"""Physical Redis reservations must survive acknowledgments from stale copies."""
import base64
import json
from uuid import uuid4

from kombu import Connection
import redis

from app.workers import tasks


def test_restored_reservation_survives_original_delivery_ack():
    broker, prefix = 'redis://host.docker.internal:55440/14', f'm5-tags-{uuid4()}:'
    client = redis.Redis.from_url(broker)
    try:
        with Connection(broker, transport_options={'global_keyprefix': prefix}) as original, Connection(broker, transport_options={'global_keyprefix': prefix}) as recovery:
            delivery = tasks.process_ingestion.apply_async(args=[str(uuid4()), str(uuid4()), 'x.pdf'], queue='ingest', connection=original)
            old = original.default_channel.basic_get('ingest', no_ack=False)
            original.default_channel.qos.restore_by_tag(old.delivery_tag)
            restored = recovery.default_channel.basic_get('ingest', no_ack=False)
            assert restored is not None
            original.default_channel.qos.ack(old.delivery_tag)  # delayed ACK from the live old owner
            assert client.hexists(prefix+'unacked', restored.delivery_tag), 'stale ACK deleted the recovery reservation'
            recovery.default_channel.qos.reject(restored.delivery_tag, requeue=True)
            again = recovery.default_channel.basic_get('ingest', no_ack=False)
            assert again is not None
            assert again.headers['id'] == delivery.id
            recovery.default_channel.qos.ack(again.delivery_tag)
    finally:
        keys = list(client.scan_iter(prefix+'*'))
        if keys: client.delete(*keys)


def test_lost_publish_reply_copies_have_independent_reservations():
    broker, prefix = 'redis://host.docker.internal:55440/14', f'm5-tags-{uuid4()}:'
    client = redis.Redis.from_url(broker)
    try:
        with Connection(broker, transport_options={'global_keyprefix': prefix}) as one, Connection(broker, transport_options={'global_keyprefix': prefix}) as two:
            delivery = tasks.process_ingestion.apply_async(args=[str(uuid4()), str(uuid4()), 'x.pdf'], queue='ingest', link=tasks.embed_document.s(), connection=one)
            envelope = client.lindex(prefix+'ingest', 0)
            client.lpush(prefix+'ingest', envelope)  # committed publish retried with identical envelope/tag
            first = one.default_channel.basic_get('ingest', no_ack=False)
            second = two.default_channel.basic_get('ingest', no_ack=False)
            assert client.hlen(prefix+'unacked') == 2, 'one reservation overwrote the other'
            one.default_channel.qos.ack(first.delivery_tag)
            assert client.hexists(prefix+'unacked', second.delivery_tag)
            assert first.headers['id'] == second.headers['id'] == delivery.id
            assert first.payload == second.payload == json.loads(base64.b64decode(json.loads(envelope)['body']))
            two.default_channel.qos.reject(second.delivery_tag, requeue=True)
            assert client.llen(prefix+'ingest') == 1
    finally:
        keys = list(client.scan_iter(prefix+'*'))
        if keys: client.delete(*keys)
