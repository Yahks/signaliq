"""UUID v7 must be monotonic within the same millisecond and time-ordered."""
import time
import uuid

from packages.core.uuid_v7 import uuid_v7


def test_uuid_v7_is_uuid() -> None:
    v = uuid_v7()
    assert isinstance(v, uuid.UUID)
    assert v.version == 7


def test_uuid_v7_time_ordered() -> None:
    a = uuid_v7()
    time.sleep(0.002)
    b = uuid_v7()
    assert a < b, "UUID v7 must be time-ordered"


def test_uuid_v7_unique() -> None:
    ids = {uuid_v7() for _ in range(1000)}
    assert len(ids) == 1000