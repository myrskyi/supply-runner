"""Telegram bot channel via long polling, so it needs no public URL. The coordinator's chat is remembered on first message."""

import json
import os
import threading
import time

import requests

from . import config

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_FILE = config.DATA / "telegram_chat.json"
API = f"https://api.telegram.org/bot{TOKEN}"
LIMIT = 4000

_started = False


def ready() -> bool:
    return bool(TOKEN)


def chat_id() -> int | None:
    return json.loads(CHAT_FILE.read_text()).get("chat_id") if CHAT_FILE.exists() else None


def send(text: str) -> str:
    chat = chat_id()
    if not (TOKEN and chat):
        return "not configured" if not TOKEN else "no chat yet"
    try:
        for start in range(0, len(text), LIMIT):
            r = requests.post(f"{API}/sendMessage", json={"chat_id": chat, "text": text[start : start + LIMIT]}, timeout=15)
            r.raise_for_status()
        return "sent"
    except Exception as e:
        return f"failed: {e}"


def _poll(on_message) -> None:
    offset = None
    while True:
        try:
            r = requests.get(f"{API}/getUpdates", params={"timeout": 25, "offset": offset}, timeout=35)
            for update in r.json().get("result", []):
                offset = update["update_id"] + 1
                message = update.get("message") or {}
                text = (message.get("text") or "").strip()
                if not text:
                    continue
                CHAT_FILE.write_text(json.dumps({"chat_id": message["chat"]["id"]}))
                if text == "/start":
                    send("Hi, I'm Supply Runner. Tell me what supplies you need, where to, and by when.")
                    continue
                on_message(text, f"telegram:{message['chat']['id']}")
        except Exception:
            time.sleep(3)


def start(on_message) -> None:
    global _started
    if TOKEN and not _started:
        _started = True
        threading.Thread(target=_poll, args=(on_message,), daemon=True).start()
