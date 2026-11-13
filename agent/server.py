"""Run: uv run uvicorn agent.server:app --port 8000"""

import json
import threading
import uuid
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import analytics, compare, config, evaluate, extract, llm, messaging, paperwork, procure, rfq, rules, search, telegram, voice

DEMO_ROUND = "r1"

@asynccontextmanager
async def lifespan(_app):
    telegram.start(procure.handle_inbound)
    yield


app = FastAPI(title="Mission Ready", lifespan=lifespan)
lock = threading.Lock()


def _empty_state() -> dict:
    return {"stage": "blocked", "search": None, "shortlist": [], "rfq": None, "sent": [], "offers": [], "decision": None, "log": []}


def _load() -> dict:
    return json.loads(config.STATE_FILE.read_text()) if config.STATE_FILE.exists() else _empty_state()


def _save(state: dict) -> None:
    config.STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def _log(state: dict, text: str) -> None:
    state["log"].append({"at": datetime.now().strftime("%H:%M:%S"), "text": text})


def _view(state: dict) -> dict:
    mission = config.load_mission()
    offers = [o | {"assessment": compare.assess(o["terms"], mission)} for o in state["offers"]]
    recommendation = compare.recommend(offers) if offers else None
    ready = state["stage"] == "ready"
    for requirement in mission["requirements"]:
        if requirement["key"] == "transport" and ready:
            requirement["status"] = "ok"
    return state | {
        "mission": mission,
        "offers": offers,
        "recommendation": recommendation,
        "rules": {"version": rules.latest(), "learned": rules.load(rules.latest()), "coordinator": rules.coordinator_rules()},
        "eval": evaluate.summary(),
        "public_url": config.PUBLIC_URL,
        "integrations": {
            "featherless": llm.available(),
            "apify": bool(config.APIFY_TOKEN),
            "posthog": analytics.client() is not None,
            "model": config.MODEL_EXTRACT,
        },
    }


def _extract_offer(carrier: str, text: str, kind: str) -> dict:
    mission = config.load_mission()
    version = rules.latest()
    terms = extract.extract(
        text, mission, rules.load(version), rules.coordinator_rules(), props={"rules_version": version, "stage": "live"}, allow_offline=True
    )
    return {
        "id": uuid.uuid4().hex[:8],
        "carrier": carrier,
        "kind": kind,
        "text": text,
        "terms": terms,
        "rules_version": version,
        "received_at": datetime.now().strftime("%H:%M:%S"),
        "rejected": False,
    }


def _add_offers(incoming: list[tuple[str, str, str]]) -> None:
    with ThreadPoolExecutor(max_workers=config.EVAL_WORKERS) as pool:
        offers = list(pool.map(lambda args: _extract_offer(*args), incoming))
    with lock:
        state = _load()
        for offer in offers:
            state["offers"].append(offer)
            t = offer["terms"]
            how = {"model": f"{config.MODEL_EXTRACT} in {t['seconds']}s", "cache": "cached model answer", "fixture": "fixture, model offline", "offline": "model offline"}[t["source"]]
            _log(state, f"Offer from {offer['carrier']}: terms extracted ({how})")
            analytics.capture("offer_received", {"carrier": offer["carrier"], "kind": offer["kind"], "source": t["source"], "rules_version": offer["rules_version"]})
        _save(state)


class RfqBody(BaseModel):
    text: str


class OfferBody(BaseModel):
    carrier: str
    text: str


class DecisionBody(BaseModel):
    offer_id: str
    action: str
    reason: str = ""


@app.get("/api/state")
def get_state():
    return _view(_load())


@app.post("/api/search")
def post_search():
    mission = config.load_mission()
    found = search.search(mission)
    picked = search.shortlist(found["carriers"], mission)
    draft = rfq.draft(mission)
    with lock:
        state = _load()
        state["search"] = {k: found[k] for k in ("source", "queries", "location", "checked_at", "cached")} | {"n_found": len(found["carriers"])}
        state["shortlist"] = picked
        state["rfq"] = draft
        state["stage"] = "shortlist"
        origin = "Apify Google Maps scraper" if found["source"] == "apify" else "placeholder fixture, Apify not connected"
        _log(state, f"Searched {len(found['queries'])} queries in {found['location']}: {len(found['carriers'])} carriers ({origin})")
        _log(state, f"Shortlisted {len(picked)} carriers by reachability and reviews")
        _log(state, f"Drafted the request for quotation ({draft['source']})")
        _save(state)
    analytics.capture("carrier_search", {"n_found": len(found["carriers"]), "n_shortlisted": len(picked), "source": found["source"]})
    return _view(state)


@app.post("/api/rfq/send")
def post_rfq_send(body: RfqBody):
    demo = [o for o in config.load_dataset()["offers"] if o["round"] == DEMO_ROUND]
    with lock:
        state = _load()
        state["rfq"] = {"text": body.text, "source": "approved"}
        state["sent"] = [{"name": o["carrier"], "kind": "demo recipient"} for o in demo] + [{"name": "Audience carrier", "kind": "reply form"}]
        state["stage"] = "waiting"
        _log(state, f"Coordinator approved the request. Sent to {len(state['sent'])} controlled demo recipients, not to real companies")
        _save(state)
    analytics.capture("rfq_sent", {"n_recipients": len(state["sent"])})
    return _view(state)


@app.post("/api/demo-replies")
def post_demo_replies():
    demo = [o for o in config.load_dataset()["offers"] if o["round"] == DEMO_ROUND]
    _add_offers([(o["carrier"], o["text"], "demo recipient") for o in demo])
    return _view(_load())


@app.post("/api/offers")
def post_offer(body: OfferBody):
    if not body.text.strip():
        raise HTTPException(400, "empty offer")
    _add_offers([(body.carrier.strip() or "Audience carrier", body.text.strip(), "reply form")])
    return {"ok": True}


@app.post("/api/decision")
def post_decision(body: DecisionBody):
    with lock:
        state = _load()
        offer = next((o for o in state["offers"] if o["id"] == body.offer_id), None)
        if offer is None:
            raise HTTPException(404, "offer not found")
        if body.action == "approve":
            state["stage"] = "ready"
            state["decision"] = {"offer_id": offer["id"], "carrier": offer["carrier"], "at": datetime.now().strftime("%Y-%m-%d %H:%M"), "by": "Mission coordinator"}
            _log(state, f"Coordinator approved {offer['carrier']}. Mission is ready to dispatch")
        elif body.action == "reject":
            offer["rejected"] = True
            offer["reject_reason"] = body.reason
            if body.reason.strip():
                rules.add_coordinator_rule(body.reason)
                _log(state, f"Coordinator rejected {offer['carrier']}. New rule saved: {body.reason.strip()}")
            else:
                _log(state, f"Coordinator rejected {offer['carrier']}")
        else:
            raise HTTPException(400, "action must be approve or reject")
        _save(state)
    analytics.capture("coordinator_decision", {"action": body.action, "carrier": offer["carrier"], "has_reason": bool(body.reason.strip())})
    return _view(state)


@app.post("/api/reset")
def post_reset(keep_rules: bool = False):
    with lock:
        _save(_empty_state())
        if not keep_rules:
            rules.clear_coordinator_rules()
    return _view(_load())


@app.get("/")
def play_page():
    return FileResponse(config.WEB / "play.html")


@app.get("/operator")
def index():
    return FileResponse(config.WEB / "index.html")


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/files/{name}")
def document_file(name: str):
    path = paperwork.OUT / name
    if "/" in name or not name.endswith(".pdf") or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="application/pdf")


@app.get("/stage")
def stage_page():
    return FileResponse(config.WEB / "stage.html")


@app.get("/mission")
def mission_page():
    return FileResponse(config.WEB / "mission.html")


class InboundBody(BaseModel):
    text: str


class SayBody(BaseModel):
    text: str = ""


@app.get("/api/procure")
def procure_state(s: str | None = None):
    with procure.use_session(s):
        return _procure_view()


def _procure_view() -> dict:
    return procure.load() | {
        "integrations": {
            "telegram": telegram.ready() and procure.meta().get("telegram_chat") is not None,
            "whatsapp": messaging.whatsapp_ready(),
            "voice": messaging.voice_ready(),
            "apify": bool(config.APIFY_TOKEN),
            "featherless": llm.available(),
            "posthog": analytics.client() is not None,
        },
        "eval": evaluate.summary(),
    }


@app.post("/api/procure/message")
def procure_message(body: InboundBody, s: str | None = None):
    procure.handle_inbound(body.text.strip(), f"web:{s}" if s else "web")
    return procure_state(s)


@app.post("/api/procure/reset")
def procure_reset(s: str | None = None):
    with procure.use_session(s), procure.lock():
        procure.save(procure.empty_state())
        return _procure_view()


@app.post("/api/procure/call/{supplier_id}")
def procure_call(supplier_id: str, simulated: bool = False, s: str | None = None):
    with procure.use_session(s):
        call_id = voice.start_call(supplier_id, simulated=simulated)
        if call_id is None:
            raise HTTPException(400, "call not started")
        if simulated:
            voice.turn(call_id, None)
        return _procure_view()


@app.post("/api/procure/call/{call_id}/say")
def procure_call_say(call_id: str, body: SayBody, s: str | None = None):
    with procure.use_session(s):
        call = next((c for c in procure.load()["calls"] if c["id"] == call_id), None)
        if call is None or call["status"] in ("done", "finishing", "no answer"):
            raise HTTPException(409, "call is over")
        reply = voice.turn(call_id, body.text.strip() or None)
        if reply["done"]:
            voice.finish(call_id)
        return _procure_view()


@app.post("/whatsapp")
async def whatsapp_webhook(request: Request):
    form = await request.form()
    text, sender = str(form.get("Body") or "").strip(), str(form.get("From") or "")
    if text:
        procure.handle_inbound(text, "whatsapp")
    return Response("<Response></Response>", media_type="application/xml")


@app.post("/voice/turn")
async def voice_turn(request: Request, call_id: str, s: str | None = None):
    form = await request.form()
    with procure.use_session(s):
        reply = voice.turn(call_id, str(form.get("SpeechResult") or "").strip() or None)
        if reply["done"]:
            procure.spawn(voice.finish, call_id)
        return Response(voice.twiml(call_id, reply), media_type="application/xml")


@app.post("/voice/status")
async def voice_status(call_id: str, s: str | None = None):
    with procure.use_session(s):
        procure.spawn(voice.finish, call_id)
    return Response("", status_code=204)


@app.get("/reply")
def reply():
    return FileResponse(config.WEB / "reply.html")


app.mount("/static", StaticFiles(directory=config.WEB), name="static")
