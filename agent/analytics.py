import atexit

from . import config

DISTINCT_ID = "mission-ready"
_client = None

if config.POSTHOG_API_KEY:
    from posthog import Posthog

    _client = Posthog(config.POSTHOG_API_KEY, host=config.POSTHOG_HOST)
    atexit.register(_client.shutdown)


def client():
    return _client


def capture(event: str, properties: dict | None = None) -> None:
    if _client is None:
        return
    try:
        _client.capture(distinct_id=DISTINCT_ID, event=event, properties=properties or {})
    except Exception as e:
        print(f"posthog capture failed: {e}")


def flush() -> None:
    if _client is not None:
        _client.flush()
