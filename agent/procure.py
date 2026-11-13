"""Procurement agent: a coordinator asks on WhatsApp for supplies, the agent finds suppliers, checks the rules, calls a supplier and reports back."""

import json
import math
import re
import threading
import uuid
from datetime import datetime

from . import analytics, config, llm, messaging, search, telegram

CONFIG_FILE = config.DATA / "procure" / "config.json"
COMPLIANCE_FILE = config.DATA / "procure" / "compliance.json"
STATE_FILE = config.DATA / "procure_state.json"
SHORTLIST_PER_CATEGORY = 3

lock = threading.RLock()


def load_config() -> dict:
    return json.loads(CONFIG_FILE.read_text())


def load_compliance() -> dict:
    return json.loads(COMPLIANCE_FILE.read_text())


def empty_state() -> dict:
    return {"stage": "idle", "request": None, "messages": [], "suppliers": [], "compliance": None, "calls": [], "steps": [], "order": None}


def load() -> dict:
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else empty_state()


def save(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def update(mutate) -> dict:
    with lock:
        state = load()
        mutate(state)
        save(state)
        return state


def step(text: str) -> None:
    update(lambda s: s["steps"].append({"at": _now(), "text": text}))


def tell_user(text: str) -> None:
    delivery = telegram.send(text) if telegram.ready() else messaging.send_whatsapp(text)
    update(lambda s: s["messages"].append({"dir": "out", "at": _now(), "text": text, "delivery": delivery}))


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


def find_suppliers(request: dict) -> list[dict]:
    cfg = load_config()
    found = []
    for key in sorted({i["category"] for i in request["items"]}):
        cat = cfg["categories"][key]
        cache_file = config.CACHE / f"suppliers_{key}.json"
        if cache_file.exists():
            result = json.loads(cache_file.read_text())
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
        ranked = sorted(candidates, key=score, reverse=True)
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
                "score": score(c),
                "price": None,
                "in_stock": None,
                "lead_time_days": None,
                "unknown": ["price", "stock", "lead time", "export documents"],
            })
        analytics.capture("supplier_search", {"category": key, "n_found": len(result["carriers"]), "n_shortlisted": min(len(ranked), SHORTLIST_PER_CATEGORY)})
    return found


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


def _supplier_lines(suppliers: list[dict]) -> str:
    lines = []
    for n, s in enumerate(suppliers, 1):
        rating = f"{s['rating']}★ ({s['reviews']} reviews)" if s.get("rating") else "no rating"
        lines.append(f"{n}. {s['name']} [{s['category_label']}], {rating}")
    return "\n".join(lines)


def run_request(text: str) -> None:
    try:
        update(lambda s: s.update(stage="working"))
        step("Reading the request")
        request = parse_request(text)
        update(lambda s: s.update(request=request))
        items = ", ".join(f"{i['quantity'] or ''} {i['name']}".strip() for i in request["items"]) or "nothing I recognise"
        tell_user(f"Got it. Looking for: {items}. Destination: {request.get('destination') or 'unknown'}. Deadline: {request.get('deadline') or 'not given'}. I'll report each step here.")

        step("Searching suppliers on Google Maps via Apify")
        suppliers = find_suppliers(request)
        update(lambda s: s.update(suppliers=suppliers))
        tell_user(f"Found suppliers near Prague, ranked by rating, reviews and how reachable they are:\n{_supplier_lines(suppliers)}\nPrices, stock and delivery time are not public. I can call them to ask.")

        step("Checking export and import rules")
        compliance = check_compliance(request)
        update(lambda s: s.update(compliance=compliance))
        applicable = [c for c in compliance["checklist"] if c["applies"]]
        lines = "\n".join(f"- {c['title']} ({c['authority']}, ~{c['lead_time_days']} d)" for c in applicable)
        tell_user(f"Paperwork you need:\n{lines}\nLongest item: ~{compliance['longest_lead_days']} days. {compliance['summary']}".strip())

        tell_user("Reply CALL <number> and I'll phone that supplier to confirm price, stock, delivery time and export documents.")
        update(lambda s: s.update(stage="waiting"))
    except Exception as e:
        step(f"Failed: {e}")
        tell_user("Something went wrong on my side. A human from the team will pick this up.")
        update(lambda s: s.update(stage="error"))


CALL_COMMAND = re.compile(r"^\s*call\s+(\d+)\s*$", re.IGNORECASE)


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
        "items": ", ".join(f"{i.get('quantity') or ''} {i['name']}".strip() for i in _items_for(state, supplier["category"])),
        "unit_price": price, "currency": facts.get("currency") or "", "quantity": quantity, "total": total,
        "lead_time_days": facts.get("lead_time_days"), "in_stock": facts.get("in_stock"), "supplier_documents": facts.get("documents"),
        "documents": [{"id": r["id"], "name": r["requester_document"], "received": False} for r in rules] + [{"id": "payment", "name": "Payment confirmation (bank transfer or receipt)", "received": False}],
    }
    update(lambda s: s.update(order=order, stage="offer"))
    step(f"Offer from {supplier['name']} sent to the coordinator for approval")
    price_text = f"{price} {order['currency']} per unit" + (f", total {total} {order['currency']}" if total else "") if price is not None else "price not confirmed"
    lead = f"ready for pickup in {order['lead_time_days']} days" if order["lead_time_days"] is not None else "pickup date not confirmed"
    tell_user(
        f"Offer from {supplier['name']} for {order['items']}: {price_text}, {lead}. "
        f"Supplier provides: {order['supplier_documents'] or 'not confirmed'}.\n"
        "Reply YES to accept, or tell me what to negotiate (for example: max 20000 CZK, pickup by 18 November)."
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
        missing = "\n".join(f"- {d['name']}" for d in order["documents"])
        tell_user(f"Agreed. To finalise the order with {order['supplier_name']} I need from you:\n{missing}\nSend them here as files, photos or a short note.")
    elif decision == "counter" and answer.get("counter"):
        from . import voice

        update(lambda s: s["order"].update(status="negotiating") or s.update(stage="negotiating"))
        step(f"Negotiating with {order['supplier_name']}: {answer['counter']}")
        analytics.capture("offer_countered", {})
        tell_user(f"OK, I'll go back to {order['supplier_name']}: {answer['counter']}")
        voice.resume(order["call_id"], answer["counter"])
    else:
        tell_user("Reply YES to accept the offer, or tell me what price or date to negotiate.")


DOCS_PROMPT = """We are collecting documents from a coordinator. Still missing:
{missing}
The coordinator just sent: "{text}"
Which of the missing items does this message provide or clearly confirm? Return one JSON object: {{"received": [ids]}}. Return only the JSON."""


def _documents_reply(text: str) -> None:
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
        prefix = f"Got it: {names}." if names else "I couldn't match that to a missing document."
        tell_user(prefix + "\nStill missing:\n" + "\n".join(f"- {d['name']}" for d in still))
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
    tell_user(f"Done. {confirmation}\nAll documents are on file. I sent the confirmation to {order['supplier_name']}.")


STAGE_HANDLERS = {"offer": _offer_reply, "documents": _documents_reply}


def handle_inbound(text: str, sender: str = "web") -> None:
    update(lambda s: s["messages"].append({"dir": "in", "at": _now(), "text": text, "from": sender}))
    analytics.capture("chat_inbound", {"channel": sender.split(":")[0], "length": len(text)})
    match = CALL_COMMAND.match(text)
    if match:
        state = load()
        index = int(match.group(1)) - 1
        if 0 <= index < len(state["suppliers"]):
            from . import voice

            threading.Thread(target=voice.start_call_or_simulate, args=(state["suppliers"][index]["id"],), daemon=True).start()
        else:
            tell_user("I don't have a supplier with that number.")
        return
    stage = load()["stage"]
    if stage in STAGE_HANDLERS:
        threading.Thread(target=STAGE_HANDLERS[stage], args=(text,), daemon=True).start()
        return
    if stage in ("working", "negotiating"):
        tell_user("Still working on it, I'll update you shortly.")
        return
    update(lambda s: s.update(request=None, suppliers=[], compliance=None, calls=[], steps=[], order=None))
    threading.Thread(target=run_request, args=(text,), daemon=True).start()
