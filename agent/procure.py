"""Procurement agent: a coordinator asks for supplies, the agent finds suppliers, checks the rules, gets a quote, collects documents and places the order.
Every coordinator (a web visitor or a Telegram chat) has its own session folder with state and feedback history."""

import contextvars
import hashlib
import json
import math
import re
import threading
import uuid
from datetime import datetime

from . import analytics, config, llm, messaging, paperwork, search, telegram

CONFIG_FILE = config.DATA / "procure" / "config.json"
COMPLIANCE_FILE = config.DATA / "procure" / "compliance.json"
SESSIONS = config.DATA / "sessions"
SESSION_ID = re.compile(r"^[a-z0-9-]{1,64}$")
HISTORY_SEED = config.DATA / "procure" / "history_seed.json"
SHORTLIST_PER_CATEGORY = 3

session = contextvars.ContextVar("session", default="default")
_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def session_id(sender: str) -> str:
    channel, _, ident = sender.partition(":")
    if channel == "web" and SESSION_ID.match(ident):
        return ident
    if channel == "telegram":
        return "tg-" + hashlib.sha256(f"{ident}:{telegram.TOKEN}".encode()).hexdigest()[:16]
    return "default"


class use_session:
    def __init__(self, sid: str | None):
        self.sid = sid if sid and SESSION_ID.match(sid) else "default"

    def __enter__(self):
        self.token = session.set(self.sid)
        return self.sid

    def __exit__(self, *exc):
        session.reset(self.token)


def spawn(fn, *args) -> None:
    threading.Thread(target=contextvars.copy_context().run, args=(fn, *args), daemon=True).start()


def session_dir():
    path = SESSIONS / session.get()
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_file():
    return session_dir() / "state.json"


def history_file():
    return session_dir() / "history.json"


def meta() -> dict:
    path = session_dir() / "meta.json"
    return json.loads(path.read_text()) if path.exists() else {}


def set_meta(**values) -> None:
    (session_dir() / "meta.json").write_text(json.dumps(meta() | values))


def shop_hint() -> str:
    if not (meta().get("telegram_chat") and config.PUBLIC_URL):
        return ""
    return f"\n\nYou play the shop: reply to the agent at {config.PUBLIC_URL}/stage?s={session.get()}"


def lock() -> threading.RLock:
    with _locks_guard:
        return _locks.setdefault(session.get(), threading.RLock())


def load_config() -> dict:
    return json.loads(CONFIG_FILE.read_text())


def load_compliance() -> dict:
    return json.loads(COMPLIANCE_FILE.read_text())


def empty_state() -> dict:
    return {"stage": "idle", "request": None, "messages": [], "suppliers": [], "compliance": None, "calls": [], "steps": [], "order": None}


def load() -> dict:
    path = state_file()
    return json.loads(path.read_text()) if path.exists() else empty_state()


def save(state: dict) -> None:
    path = state_file()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    tmp.replace(path)


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def update(mutate) -> dict:
    with lock():
        state = load()
        mutate(state)
        save(state)
        return state


def step(text: str) -> None:
    update(lambda s: s["steps"].append({"at": _now(), "text": text}))


def _deliver(send_telegram, send_whatsapp) -> str:
    chat = meta().get("telegram_chat")
    if chat:
        return send_telegram(chat)
    if session.get() == "default":
        return send_whatsapp()
    return "web"


def send_file(path, caption: str = "") -> None:
    delivery = _deliver(lambda chat: telegram.send_document(path, caption, chat), lambda: "not sent")
    update(lambda s: s["messages"].append({"dir": "out", "at": _now(), "text": caption or path.name, "file": path.name, "delivery": delivery}))


def tell_user(text: str, buttons: list | None = None) -> None:
    delivery = _deliver(lambda chat: telegram.send(text, buttons, chat), lambda: messaging.send_whatsapp(text))
    rows = [row if isinstance(row, list) else [row] for row in buttons or []]
    update(lambda s: s["messages"].append({"dir": "out", "at": _now(), "text": text, "buttons": rows, "delivery": delivery}))


REQUEST_PROMPT = """A coordinator of a humanitarian organisation wrote this message:
---
{text}
---
Categories you can use: {categories}.
Return one JSON object:
- items: array of {{"name": short English name, "quantity": number or null, "category": one of the categories}}
- destination: city or region, or null
- deadline: "YYYY-MM-DD" or null (today is {today})
- own_transport: true, false or null
- notes: short English string with anything else important, or ""
Return only the JSON."""


def parse_request(text: str) -> dict:
    cfg = load_config()
    prompt = REQUEST_PROMPT.format(text=text, categories=", ".join(f"{k} ({v['label']})" for k, v in cfg["categories"].items()), today=datetime.now().date().isoformat())
    request, _ = llm.chat_json([{"role": "user", "content": prompt}], config.MODEL_EXTRACT, props={"stage": "parse_request"})
    request["items"] = [i for i in request.get("items") or [] if i.get("category") in cfg["categories"]]
    return request


def _distance_km(c: dict, origin: tuple[float, float] | None) -> float | None:
    if not origin or c.get("lat") is None:
        return None
    lat1, lng1, lat2, lng2 = map(math.radians, (origin[0], origin[1], c["lat"], c["lng"]))
    a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return round(6371 * 2 * math.asin(math.sqrt(a)), 1)


def score(c: dict) -> float:
    reachable = bool(c.get("phone")) + bool(c.get("emails")) + bool(c.get("website"))
    rating = (c.get("rating") or 0) * min(1.0, math.log10(1 + (c.get("reviews") or 0)) / 2)
    return round(rating + reachable * 0.5, 2)


def history() -> list[dict]:
    seed = json.loads(HISTORY_SEED.read_text())["orders"] if HISTORY_SEED.exists() else []
    path = history_file()
    live = json.loads(path.read_text()) if path.exists() else []
    return seed + live


def _is_supplier(entry: dict, name: str) -> bool:
    return entry.get("supplier") == name or entry.get("supplier_sha256") == hashlib.sha256(name.encode()).hexdigest()


def our_record(name: str) -> dict | None:
    past = [h for h in history() if _is_supplier(h, name)]
    if not past:
        return None
    return {"rating": round(sum(h["rating"] for h in past) / len(past), 1), "orders": len(past), "last_comment": past[-1].get("comment") or "", "alias": past[-1].get("alias")}


def rank(c: dict, weight: float) -> float:
    record = our_record(c["name"])
    return round(score(c) + (weight * (record["rating"] - 3) if record else 0), 2)


def find_suppliers(request: dict) -> tuple[list[dict], list[dict]]:
    cfg = load_config()
    found, demoted = [], []
    for key in sorted({i["category"] for i in request["items"]}):
        cat = cfg["categories"][key]
        cache_file = config.CACHE / f"suppliers_{key}.json"
        snapshot = config.DATA / "procure" / f"suppliers_{key}.json"
        if cache_file.exists() or snapshot.exists():
            result = json.loads((cache_file if cache_file.exists() else snapshot).read_text())
        elif config.APIFY_TOKEN:
            mission = {"search": {"queries": cat["queries"], "location": cfg["pickup_area"], "language": cfg["language"], "max_per_query": cfg["max_per_query"]}}
            result = search.scrape(mission, cache_file)
        else:
            continue
        lats = [c["lat"] for c in result["carriers"] if c.get("lat")]
        lngs = [c["lng"] for c in result["carriers"] if c.get("lng")]
        origin = (sum(lats) / len(lats), sum(lngs) / len(lngs)) if lats else None
        allowed = cat.get("google_categories")
        candidates = [c for c in result["carriers"] if c.get("name") and (c.get("phone") or c.get("emails")) and (not allowed or c.get("category") in allowed)]
        weight = cfg.get("feedback_weight", 0)
        ranked = sorted(candidates, key=lambda c: rank(c, weight), reverse=True)
        by_google = sorted(candidates, key=score, reverse=True)[:SHORTLIST_PER_CATEGORY]
        shortlist = {c["name"] for c in ranked[:SHORTLIST_PER_CATEGORY]}
        demoted += [{"name": our_record(c["name"])["alias"] or c["name"], "ours": our_record(c["name"])} for c in by_google if c["name"] not in shortlist and our_record(c["name"])]
        for c in ranked[:SHORTLIST_PER_CATEGORY]:
            found.append({
                "id": uuid.uuid4().hex[:6],
                "category": key,
                "category_label": cat["label"],
                "name": c["name"],
                "google_category": c.get("category"),
                "rating": c.get("rating"),
                "reviews": c.get("reviews"),
                "phone": c.get("phone"),
                "email": (c.get("emails") or [None])[0],
                "website": c.get("website"),
                "source_url": c.get("source_url"),
                "address": c.get("address"),
                "km_from_centre": _distance_km(c, origin),
                "score": rank(c, weight),
                "ours": our_record(c["name"]),
                "price": None,
                "in_stock": None,
                "lead_time_days": None,
                "unknown": ["price", "stock", "lead time", "export documents"],
            })
        analytics.capture("supplier_search", {"category": key, "n_found": len(result["carriers"]), "n_shortlisted": min(len(ranked), SHORTLIST_PER_CATEGORY)})
    return found, demoted


COMPLIANCE_PROMPT = """You prepare a compliance checklist for a humanitarian delivery.
Request: {request}
Route: suppliers in the Czech Republic, the organisation's own van, destination {destination} in Ukraine.

Here is the ONLY rule list you may use:
{rules}

For each rule decide if it applies to this request. Return one JSON object:
{{"checklist": [{{"id": rule id, "applies": true or false, "why": one short English sentence about this request}}],
  "summary": two short English sentences for the coordinator, naming the longest lead time}}
Never invent rules that are not in the list. Never sound more certain than the rule summary: if a rule depends on the exact model or its status is unconfirmed, mark it as applying and say what must be checked. Return only the JSON."""


def check_compliance(request: dict) -> dict:
    knowledge = load_compliance()
    rules = [r for r in knowledge["rules"] if set(r["applies_to"]) & {i["category"] for i in request["items"]}]
    prompt = COMPLIANCE_PROMPT.format(
        request=json.dumps(request["items"], ensure_ascii=False),
        destination=request.get("destination") or "unknown",
        rules=json.dumps([{k: r[k] for k in ("id", "applies_to", "title", "summary")} for r in rules], ensure_ascii=False, indent=1),
    )
    try:
        answer, _ = llm.chat_json([{"role": "user", "content": prompt}], config.MODEL_EXTRACT, props={"stage": "compliance"})
        decided = {c.get("id"): c for c in answer.get("checklist") or []}
        summary = answer.get("summary") or ""
    except llm.LLMUnavailable:
        decided, summary = {}, ""
    by_id = {r["id"]: r for r in rules}
    checklist = [
        by_id[rid] | {"applies": bool(decided.get(rid, {}).get("applies", True)), "why": decided.get(rid, {}).get("why", "")}
        for rid in by_id
    ]
    applicable = [c for c in checklist if c["applies"]]
    longest = max((c["lead_time_days"] for c in applicable), default=0)
    analytics.capture("compliance_check", {"n_rules": len(rules), "n_applicable": len(applicable), "longest_lead_days": longest})
    return {"checklist": checklist, "summary": summary, "longest_lead_days": longest, "disclaimer": knowledge["disclaimer"]}


def short_name(name: str) -> str:
    return " ".join(re.split(r"\s+[-–|]\s+|,\s", name)[0].split()[:4])


def _bullets(lines) -> str:
    return "\n".join(f"• {line}" for line in lines)


def _supplier_lines(suppliers: list[dict]) -> str:
    groups: dict[str, list[str]] = {}
    for n, s in enumerate(suppliers, 1):
        rating = f"{s['rating']}★ ({s['reviews']})" if s.get("rating") else "no rating"
        ours = s.get("ours")
        past = f" · our orders {ours['rating']:g}/5" if ours else ""
        groups.setdefault(s["category_label"], []).append(f"{n}. {short_name(s['name'])} · {rating}{past}")
    return "\n\n".join(f"{label}\n" + "\n".join(lines) for label, lines in groups.items())


def run_request(text: str) -> None:
    try:
        update(lambda s: s.update(stage="working"))
        step("Reading the request")
        request = parse_request(text)
        update(lambda s: s.update(request=request))
        items = ", ".join(f"{i['quantity'] or ''} {i['name']}".strip() for i in request["items"]) or "nothing I recognise"
        trip = " · ".join(x for x in (f"To {request['destination']}" if request.get("destination") else "", f"by {datetime.fromisoformat(request['deadline']).strftime('%-d %b')}" if request.get("deadline") else "", "own van" if request.get("own_transport") else "") if x)
        wanted = _bullets(f"{i['quantity']} × {i['name']}" if i["quantity"] else i["name"] for i in request["items"]) or "No items recognised"
        tell_user(f"Request received\n{wanted}\n{trip}\n\nSearching suppliers and checking paperwork.")

        step("Searching suppliers on Google Maps via Apify")
        suppliers, demoted = find_suppliers(request)
        update(lambda s: s.update(suppliers=suppliers, demoted=demoted))
        skipped = "".join(f"\n\nExcluded: {short_name(d['name'])}, our orders {d['ours']['rating']:g}/5 ({d['ours']['last_comment']})" for d in demoted)
        tell_user(f"Suppliers near Prague\n\n{_supplier_lines(suppliers)}{skipped}")

        step("Checking export and import rules")
        compliance = check_compliance(request)
        update(lambda s: s.update(compliance=compliance))
        applicable = [c for c in compliance["checklist"] if c["applies"]]
        longest = max(applicable, key=lambda c: c["lead_time_days"], default=None)
        docs = _bullets(f"{c['title']} · ~{c['lead_time_days']} d" for c in applicable)
        tell_user(f"Paperwork\n{docs}" + (f"\n\nStart first: {longest['title']} (~{longest['lead_time_days']} days)" if longest else ""))

        tell_user("Prices and stock are not public. Shall I ask the top supplier for a quote, or name another one?", [(f"💬 Ask {short_name(suppliers[0]['name'])} for a quote", "ask 1")] if suppliers else None)
        update(lambda s: s.update(stage="waiting"))
    except Exception as e:
        step(f"Failed: {e}")
        tell_user("Something went wrong on my side. A human from the team will pick this up.")
        update(lambda s: s.update(stage="error"))


CALL_COMMAND = re.compile(r"^\s*(?:(?:call|ask|contact|message)\s+)?(?:no\.?\s*|#)?(\d+)\s*$", re.IGNORECASE)


def picked_supplier(text: str, state: dict) -> int | None:
    match = CALL_COMMAND.match(text)
    if match:
        return int(match.group(1)) - 1
    if state["stage"] != "waiting" or not re.search(r"\b(call|phone|ring|contact|ask|message|write|quote)\b", text, re.IGNORECASE):
        return None
    words = set(re.findall(r"[\w.-]+", text.lower()))
    for n, s in enumerate(state["suppliers"]):
        if short_name(s["name"]).split()[0].lower() in words:
            return n
    return None


def _items_for(state: dict, category: str) -> list[dict]:
    return [i for i in (state.get("request") or {}).get("items", []) if i["category"] == category]


def present_offer(call_id: str) -> None:
    state = load()
    call = next(c for c in state["calls"] if c["id"] == call_id)
    supplier = next(s for s in state["suppliers"] if s["id"] == call["supplier_id"])
    facts = call.get("facts") or {}
    quantity = sum(i.get("quantity") or 0 for i in _items_for(state, supplier["category"])) or None
    price = facts.get("unit_price")
    total = price * quantity if price is not None and quantity else None
    rules = [c for c in (state.get("compliance") or {}).get("checklist", []) if c["applies"] and c.get("requester_document")]
    order = {
        "supplier_id": supplier["id"], "supplier_name": supplier["name"], "call_id": call_id, "status": "offer",
        "items": ", ".join(f"{i['quantity']} × {i['name']}" if i.get("quantity") else i["name"] for i in _items_for(state, supplier["category"])),
        "unit_price": price, "currency": facts.get("currency") or "", "quantity": quantity, "total": total,
        "lead_time_days": facts.get("lead_time_days"), "in_stock": facts.get("in_stock"), "supplier_documents": facts.get("documents"),
        "documents": [{"id": r["id"], "name": r["requester_document"], "received": False} for r in rules] + [{"id": "payment", "name": "Payment confirmation (bank transfer or receipt)", "received": False}],
    }
    update(lambda s: s.update(order=order, stage="offer"))
    step(f"Offer from {supplier['name']} sent to the coordinator for approval")
    price_text = f"{price} {order['currency']} per unit" + (f", total {total} {order['currency']}" if total else "") if price is not None else "Price not confirmed"
    lead = f"Pickup in {order['lead_time_days']} day{'' if order['lead_time_days'] == 1 else 's'}" if order["lead_time_days"] is not None else "Pickup date not confirmed"
    tell_user(
        f"Offer from {short_name(supplier['name'])}\n{order['items']}\n{price_text}\n{lead}\nDocuments: {order['supplier_documents'] or 'not confirmed'}\n\n"
        "Accept, or reply with a counter (e.g. max 20000 CZK, pickup tomorrow).",
        [("✅ Accept offer", "YES")],
    )


OFFER_PROMPT = """The coordinator received a supplier offer: {offer}
Their reply: "{text}"
Return one JSON object: {{"decision": "accept" or "counter" or "unclear", "counter": if counter, one short English instruction for our agent to negotiate with the supplier, else ""}}
Return only the JSON."""


def _offer_reply(text: str) -> None:
    order = load()["order"]
    offer = {k: order[k] for k in ("supplier_name", "items", "unit_price", "currency", "total", "lead_time_days")}
    answer, _ = llm.chat_json([{"role": "user", "content": OFFER_PROMPT.format(offer=json.dumps(offer, ensure_ascii=False), text=text)}], config.MODEL_EXTRACT, props={"stage": "offer_reply"})
    decision = answer.get("decision")
    if decision == "accept":
        update(lambda s: s["order"].update(status="documents") or s.update(stage="documents"))
        step("Coordinator accepted the offer, collecting documents and payment")
        analytics.capture("offer_accepted", {"total": order["total"], "currency": order["currency"]})
        missing = _bullets(d["name"] for d in order["documents"])
        tell_user(f"Accepted. To place the order, send:\n{missing}\n\nFiles, photos or a short note are fine.")
        send_file(paperwork.purchase_order(order), "Purchase order draft")
    elif decision == "counter" and answer.get("counter"):
        from . import voice

        update(lambda s: s["order"].update(status="negotiating") or s.update(stage="negotiating"))
        step(f"Negotiating with {order['supplier_name']}: {answer['counter']}")
        analytics.capture("offer_countered", {})
        tell_user(f"Negotiating with {short_name(order['supplier_name'])}: {answer['counter']}")
        voice.resume(order["call_id"], answer["counter"])
    else:
        tell_user("Reply YES to accept, or send a counter (price or date).")


DOCS_PROMPT = """We are collecting documents from a coordinator. Still missing:
{missing}
The coordinator just sent: "{text}"
Which of the missing items does this message provide? Count an item only if the message attaches a file whose name matches it,
or names that specific document, or (for payment) says the payment was made. Vague phrases like "docs ready" or a rating do not count. Return one JSON object: {{"received": [ids]}}. Return only the JSON."""


def _documents_reply(text: str) -> None:
    with lock():
        if load()["stage"] == "documents":
            _match_documents(text)


def _match_documents(text: str) -> None:
    order = load()["order"]
    missing = [d for d in order["documents"] if not d["received"]]
    answer, _ = llm.chat_json([{"role": "user", "content": DOCS_PROMPT.format(missing=json.dumps([{"id": d["id"], "name": d["name"]} for d in missing]), text=text)}], config.MODEL_EXTRACT, props={"stage": "documents"})
    got = set(answer.get("received") or []) & {d["id"] for d in missing}

    def mark(s):
        for d in s["order"]["documents"]:
            if d["id"] in got:
                d.update(received=True, at=_now())

    order = update(mark)["order"]
    still = [d for d in order["documents"] if not d["received"]]
    names = ", ".join(d["name"] for d in order["documents"] if d["id"] in got)
    if names:
        step(f"Received: {names}")
    if still:
        prefix = f"Received: {names}" if names else "Could not match that to a missing document."
        tell_user(prefix + "\n\nStill needed:\n" + _bullets(d["name"] for d in still))
    else:
        finalize()


def finalize() -> None:
    order = load()["order"]
    number = "SR-" + uuid.uuid4().hex[:6].upper()
    pickup = None
    if order.get("lead_time_days") is not None:
        from datetime import timedelta

        pickup = (datetime.now() + timedelta(days=int(order["lead_time_days"]))).strftime("%a %d %b")
    update(lambda s: s["order"].update(status="ordered", number=number, pickup=pickup) or s.update(stage="ordered"))
    confirmation = f"Order {number} confirmed: {order['items']}" + (f", {order['total']} {order['currency']} paid" if order.get("total") else "") + (f", pickup {pickup}" if pickup else "") + ". Our van driver will bring the power of attorney."
    from . import voice

    voice.notify_supplier(order["call_id"], confirmation)
    step(f"Order {number} confirmed with {order['supplier_name']}")
    analytics.capture("order_finalized", {"total": order.get("total"), "currency": order.get("currency"), "n_documents": len(order["documents"])})
    paid = f"\n{order['total']} {order['currency']} paid" if order.get("total") else ""
    tell_user(f"✅ Order {number} confirmed\n{order['items']}{paid}\n" + (f"Pickup {pickup} at {short_name(order['supplier_name'])}\n" if pickup else "") + "\nAll documents on file. Supplier notified.")
    send_file(paperwork.order_confirmation(load()["order"]), f"Order {number} confirmation")
    update(lambda s: s.update(stage="feedback"))
    tell_user(f"After pickup: how did it go with {short_name(order['supplier_name'])}?\nRate 1 to 5, a comment is welcome. It shapes the supplier ranking next time.", [[(f"{n} ⭐", str(n)) for n in range(1, 6)]])


RATING = re.compile(r"\b([1-5])\b")


def _feedback_reply(text: str) -> None:
    match = RATING.search(text)
    if not match:
        tell_user("Please reply with a number from 1 to 5.")
        return
    order = load()["order"]
    rating = int(match.group(1))
    comment = RATING.sub("", text, count=1).strip(" ,.-:/") or ""
    supplier = next((s for s in load()["suppliers"] if s["id"] == order["supplier_id"]), {})
    entry = {"supplier": order["supplier_name"], "category": supplier.get("category"), "rating": rating, "comment": comment, "order": order.get("number"), "at": datetime.now().date().isoformat()}
    with lock():
        path = history_file()
        live = json.loads(path.read_text()) if path.exists() else []
        path.write_text(json.dumps(live + [entry], ensure_ascii=False, indent=2))
    record = our_record(order["supplier_name"])
    update(lambda s: s["order"].update(feedback={"rating": rating, "comment": comment}) or s.update(stage="closed"))
    step(f"Feedback saved: {rating}/5 for {order['supplier_name']}")
    analytics.capture("supplier_feedback", {"rating": rating, "has_comment": bool(comment), "category": entry["category"]})
    tell_user(f"Thanks, saved.\n{short_name(order['supplier_name'])}: {record['rating']:g}/5 from {record['orders']} order(s) with us. {'Ranks higher next time.' if rating >= 4 else 'Ranks lower next time.' if rating <= 2 else 'Ranking stays about the same.'}")


STAGE_HANDLERS = {"offer": _offer_reply, "documents": _documents_reply, "feedback": _feedback_reply}


def handle_inbound(text: str, sender: str = "web") -> None:
    with use_session(session_id(sender)):
        if sender.startswith("telegram:"):
            set_meta(telegram_chat=int(sender.split(":", 1)[1]))
        _handle(text, sender)


def _handle(text: str, sender: str) -> None:
    update(lambda s: s["messages"].append({"dir": "in", "at": _now(), "text": text, "from": sender}))
    analytics.capture("chat_inbound", {"channel": sender.split(":")[0], "length": len(text)})
    state = load()
    index = picked_supplier(text, state) if state["stage"] not in STAGE_HANDLERS else None
    if index is not None:
        if 0 <= index < len(state["suppliers"]):
            from . import voice

            spawn(voice.start_call_or_simulate, state["suppliers"][index]["id"])
        else:
            tell_user("I don't have a supplier with that number.")
        return
    stage = load()["stage"]
    if stage in STAGE_HANDLERS:
        spawn(STAGE_HANDLERS[stage], text)
        return
    if stage in ("working", "negotiating"):
        tell_user("Still working on it, I'll update you shortly.")
        return
    update(lambda s: s.update(request=None, suppliers=[], compliance=None, calls=[], steps=[], order=None))
    spawn(run_request, text)
