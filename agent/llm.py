import hashlib
import json
import re
import time
import uuid

from . import analytics, config


class LLMUnavailable(Exception):
    pass


_client = None


def _get_client():
    global _client
    if _client is None and config.FEATHERLESS_API_KEY:
        kwargs = {"base_url": config.FEATHERLESS_BASE_URL, "api_key": config.FEATHERLESS_API_KEY}
        if analytics.client() is not None:
            from posthog.ai.openai import OpenAI

            _client = OpenAI(posthog_client=analytics.client(), **kwargs)
        else:
            from openai import OpenAI

            _client = OpenAI(**kwargs)
    return _client


def available() -> bool:
    return bool(config.FEATHERLESS_API_KEY)


def _cache_path(model: str, messages: list, temperature: float):
    key = hashlib.sha256(json.dumps([model, messages, temperature], ensure_ascii=False).encode()).hexdigest()
    return config.LLM_CACHE / f"{key}.json"


def chat(messages: list, model: str, *, props: dict | None = None, temperature: float = 0.0, max_tokens: int = 1500) -> dict:
    """Returns {"text", "cached", "seconds"}. Cached answers keep the demo alive when the network is down."""
    path = _cache_path(model, messages, temperature)
    if path.exists():
        return {"text": json.loads(path.read_text())["text"], "cached": True, "seconds": 0.0}

    client = _get_client()
    if client is None:
        raise LLMUnavailable("FEATHERLESS_API_KEY is not set and no cached answer exists")

    kwargs = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    if analytics.client() is not None:
        kwargs["posthog_distinct_id"] = analytics.DISTINCT_ID
        kwargs["posthog_trace_id"] = str(uuid.uuid4())
        kwargs["posthog_properties"] = props or {}

    started = time.time()
    last_error = None
    for attempt in range(6):
        try:
            response = client.chat.completions.create(**kwargs)
            text = response.choices[0].message.content or ""
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
            path.write_text(json.dumps({"model": model, "text": text}, ensure_ascii=False))
            return {"text": text, "cached": False, "seconds": round(time.time() - started, 1)}
        except Exception as e:
            last_error = e
            status = getattr(e, "status_code", None)
            if status is not None and status not in (408, 429, 500, 502, 503, 504):
                break
            time.sleep(min(2 ** attempt, 20))
    raise LLMUnavailable(f"{model}: {last_error}")


def parse_json(text: str) -> dict:
    text = re.sub(r"```(?:json)?", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in model output")
    return json.loads(text[start : end + 1])


def chat_json(messages: list, model: str, *, props: dict | None = None) -> tuple[dict, dict]:
    result = chat(messages, model, props=props)
    try:
        return parse_json(result["text"]), result
    except ValueError:
        retry = messages + [
            {"role": "assistant", "content": result["text"]},
            {"role": "user", "content": "That was not valid JSON. Return only the JSON object."},
        ]
        result = chat(retry, model, props=props)
        return parse_json(result["text"]), result
