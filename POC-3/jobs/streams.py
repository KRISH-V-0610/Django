"""Names, limits and the Redis connection for the job stream."""
import redis
from django.conf import settings

STREAM = "jobs"            # where producers XADD new jobs
GROUP = "workers"          # the consumer group: each message goes to ONE worker of the group
DLQ_STREAM = "jobs:dlq"    # dead-letter stream: jobs that failed too many times

MAX_ATTEMPTS = 3           # delivery attempts before a job is dead-lettered
RETRY_BASE_MS = 2000       # wait before attempt 2; doubles each time: 2s, 4s, 8s ...

_client = None


def get_redis():
    global _client
    if _client is None:
        _client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _client


def ensure_group(client=None):
    """Create the consumer group (and the stream) if they do not exist yet."""
    client = client or get_redis()
    try:
        # id="0": a brand-new group starts from the beginning, so messages that were
        # produced before the first worker started are not lost.
        client.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    except redis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):  # BUSYGROUP = the group already exists, fine
            raise
