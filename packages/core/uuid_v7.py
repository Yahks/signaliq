"""
UUID v7 (time-ordered) generator.

Postgres <18 has no native uuidv7(). We generate them in Python.
Used for primary keys on high-write tables.
"""
import os
import time
import uuid


def uuid_v7() -> uuid.UUID:
    """
    Generate a UUID v7: 48-bit unix-ms timestamp + 74 random bits.

    Layout (RFC 9562):
    | 48 bits ms | 4 bits ver | 12 bits rand_a | 2 bits var | 62 bits rand_b |
    """
    unix_ms = int(time.time() * 1000) & 0xFFFFFFFFFFFF

    rand_a = int.from_bytes(os.urandom(2), "big") & 0x0FFF
    rand_b = int.from_bytes(os.urandom(8), "big") & 0x3FFFFFFFFFFFFFFF

    value = (
        (unix_ms << 80)
        | (0x7 << 76)
        | (rand_a << 64)
        | (0b10 << 62)
        | rand_b
    )
    return uuid.UUID(int=value)