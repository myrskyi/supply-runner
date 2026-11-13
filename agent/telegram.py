"""Telegram bot channel via long polling, so it needs no public URL. Every chat is its own coordinator session."""

import os
import threading
import time

import requests

from . import config

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
API = f"https://api.telegram.org/bot{TOKEN}"
LIMIT = 4000

_started = False


def ready() -> bool:
    return bool(TOKEN)


GREETING = "Hi, I'm Supply Runner. I find suppliers near Prague, get a quote and collect the paperwork.\n\nTry: Need 20 first aid kits with tourniquets and 2 thermal imaging monoculars, own van, Kharkiv, by 20 November"


def send(text: str, buttons: list | None = None, chat: int | None = None) -> str:
    if not (TOKEN and chat):
        return "not configured" if not TOKEN else "no chat yet"
    try:
        chunks = [text[start : start + LIMIT] for start in range(0, len(text), LIMIT)]
        for n, chunk in enumerate(chunks):
            body = {"chat_id": chat, "text": chunk}
            if buttons and n == len(chunks) - 1:
                body["reply_markup"] = {"inline_keyboard": [[{"text": label, "callback_data": data} for label, data in (row if isinstance(row, list) else [row])] for row in buttons]}
            r = requests.post(f"{API}/sendMessage", json=body, timeout=15)
            r.raise_for_status()
        return "sent"
    except Exception as e:
        return f"failed: {e}"


def send_document(path, caption: str = "", chat: int | None = None) -> str:
    if not (TOKEN and chat):
        return "not configured" if not TOKEN else "no chat yet"
    try:
        with open(path, "rb") as f:
            r = requests.post(f"{API}/sendDocument", data={"chat_id": chat, "caption": caption}, files={"document": f}, timeout=30)
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
                if query := update.get("callback_query"):
                    requests.post(f"{API}/answerCallbackQuery", json={"callback_query_id": query["id"]}, timeout=15)
                    requests.post(f"{API}/editMessageReplyMarkup", json={"chat_id": query["message"]["chat"]["id"], "message_id": query["message"]["message_id"]}, timeout=15)
                    on_message(query["data"], f"telegram:{query['message']['chat']['id']}")
                    continue
                message = update.get("message") or {}
                text = (message.get("text") or message.get("caption") or "").strip()
                if message.get("document"):
                    text = f"{text} [attached file: {message['document'].get('file_name', 'document')}]".strip()
                elif message.get("photo"):
                    text = f"{text} [attached photo]".strip()
                if not text:
                    continue
                if text == "/start":
                    send(GREETING, chat=message["chat"]["id"])
                    continue
                on_message(text, f"telegram:{message['chat']['id']}")
        except Exception:
            time.sleep(3)


def start(on_message) -> None:
    global _started
    if TOKEN and not _started:
        _started = True
        threading.Thread(target=_poll, args=(on_message,), daemon=True).start()
