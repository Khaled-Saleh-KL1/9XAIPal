"""Give each physical Redis receipt an independent acknowledgment identity.

Kombu persists delivery_tag in the envelope, including on restoration/retry.
Concurrent copies must not overwrite one unacked hash entry, or let an old
owner's ACK erase a new recovery reservation. Logical task/canvas ids stay put.
"""
from uuid import uuid4

from kombu.transport.redis import Channel


class ReservationMessage(Channel.Message):
    def __init__(self, payload, channel=None, **kwargs):
        properties = {**payload['properties'], 'delivery_tag': str(uuid4())}
        super().__init__({**payload, 'properties': properties}, channel=channel, **kwargs)


# The Redis channel constructs this message before qos.append, for both real
# consumers and basic_get. Other broker transports retain their own semantics.
Channel.Message = ReservationMessage
