"""A. UUIDv7 - run with: uv run python -m python_features.uuid_demo"""
import uuid
from datetime import datetime, timezone


def uuid7_timestamp(u: uuid.UUID) -> datetime:
    """A UUIDv7 starts with a 48-bit Unix time in milliseconds. Read it back."""
    ms = int.from_bytes(u.bytes[:6], "big")  # first 6 bytes = 48 bits
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


if __name__ == "__main__":
    a, b = uuid.uuid4(), uuid.uuid7()
    print("v4:", a, "version =", a.version)
    print("v7:", b, "version =", b.version)
    print("time hidden inside the v7:", uuid7_timestamp(b))

    v4s = [uuid.uuid4() for _ in range(5)]
    v7s = [uuid.uuid7() for _ in range(5)]
    print("\nv4 in creation order sorted?", v4s == sorted(v4s))  # almost always False
    print("v7 in creation order sorted?", v7s == sorted(v7s))    # True
