"""Voice call to a supplier. Twilio does speech-to-text and text-to-speech, the model decides what to say next."""

import uuid

from . import analytics, config, llm, messaging, procure

MAX_TURNS = 14

AGENT_PROMPT = """You are an assistant phoning a supplier on behalf of a humanitarian organisation from Ukraine.
Supplier: {supplier}.
We want to buy: {items}.
We pick up with our own van near Prague and drive to {destination}.
Find out only what a supplier can know: unit price and currency, whether it is in stock, how many days until pickup,
and which documents they can provide (invoice, CE declaration of conformity, export control classification of the exact model).
Paperwork we must check ourselves: {documents}. Do not ask the supplier about licence fees or government procedures.
Rules: be polite and brief, at most 2 short sentences per turn, ask one thing at a time, never ask again what the supplier
already answered, never invent facts, never agree to buy or pay. When you know the answers, or the supplier cannot help,
thank them and end.
Instructions from our coordinator (follow them, they override earlier answers): {instructions}

Conversation so far:
{transcript}

Return one JSON object: {{"say": what you say next, "done": true if this is your goodbye}}. Return only the JSON."""

FACTS_PROMPT = """Phone call transcript between our assistant and a supplier:
{transcript}

Return one JSON object with what the SUPPLIER said (null if not said):
{{"unit_price": number or null, "currency": "CZK" or "EUR" or null, "in_stock": true/false/null,
  "lead_time_days": number or null, "documents": short English string or null,
  "control_classification": short string or null, "summary": one English sentence}}
Return only the JSON."""


def _call(state: dict, call_id: str) -> dict:
    return next(c for c in state["calls"] if c["id"] == call_id)


def _transcript(call: dict) -> str:
    return "\n".join(f"{'Assistant' if t['who'] == 'agent' else 'Supplier'}: {t['text']}" for t in call["transcript"]) or "(call just connected)"


def start_call(supplier_id: str, simulated: bool = False) -> str | None:
    state = procure.load()
    supplier = next((s for s in state["suppliers"] if s["id"] == supplier_id), None)
    if supplier is None:
        return None
    if not simulated and not messaging.voice_ready():
        procure.tell_user("Phone calls are not set up yet (Twilio keys missing).")
        return None
    call_id = uuid.uuid4().hex[:8]
    call = {
        "id": call_id,
        "supplier_id": supplier_id,
        "supplier_name": supplier["name"],
        "status": "dialling",
        "simulated": simulated,
        "demo_line": True,
        "transcript": [],
        "facts": None,
    }
    procure.update(lambda s: s["calls"].append(call))
    procure.step(f"Calling {supplier['name']} (demo line, a teammate plays the supplier)")
    procure.tell_user(f"📞 Calling {procure.short_name(supplier['name'])} for price, stock and pickup date.")
    analytics.capture("supplier_call_started", {"simulated": simulated, "category": supplier["category"]})
    if not simulated:
        try:
            sid = messaging.place_call(call_id)
            procure.update(lambda s: _call(s, call_id).update(sid=sid))
        except Exception as e:
            procure.update(lambda s: _call(s, call_id).update(status="failed"))
            procure.tell_user(f"The call could not be placed: {e}")
    return call_id


def start_call_or_simulate(supplier_id: str) -> str | None:
    simulated = not messaging.voice_ready()
    call_id = start_call(supplier_id, simulated=simulated)
    if call_id and simulated:
        turn(call_id, None)
    return call_id


def turn(call_id: str, supplier_said: str | None) -> dict:
    """Records what the supplier said and returns {"say", "done"} for the next agent line."""
    state = procure.load()
    call = _call(state, call_id)
    supplier = next(s for s in state["suppliers"] if s["id"] == call["supplier_id"])
    request = state["request"] or {}
    if supplier_said:
        call["transcript"].append({"who": "supplier", "text": supplier_said})
    items = ", ".join(f"{i.get('quantity') or ''} {i['name']}".strip() for i in request.get("items", []) if i["category"] == supplier["category"])
    documents = ", ".join(c["title"] for c in ((state.get("compliance") or {}).get("checklist") or []) if c["applies"] and supplier["category"] in c["applies_to"])
    agent_turns = sum(t["who"] == "agent" for t in call["transcript"])
    if agent_turns >= MAX_TURNS:
        reply = {"say": "Thank you very much, that's all I needed. Goodbye.", "done": True}
    else:
        prompt = AGENT_PROMPT.format(
            supplier=supplier["name"], items=items or "the items", destination=request.get("destination") or "Ukraine",
            documents=documents or "invoice and packing list", transcript=_transcript(call),
            instructions="; ".join(call.get("instructions") or []) or "none",
        )
        try:
            answer, _ = llm.chat_json([{"role": "user", "content": prompt}], config.MODEL_EXTRACT, props={"stage": "voice_turn", "call_id": call_id})
            reply = {"say": str(answer.get("say") or "Sorry, could you repeat that?"), "done": bool(answer.get("done"))}
        except llm.LLMUnavailable:
            reply = {"say": "Sorry, I have a technical problem. We will write to you by email. Goodbye.", "done": True}
    call["transcript"].append({"who": "agent", "text": reply["say"]})
    call["status"] = "finishing" if reply["done"] else "talking"
    procure.update(lambda s: _call(s, call_id).update(transcript=call["transcript"], status=call["status"]))
    return reply


def finish(call_id: str) -> None:
    state = procure.load()
    call = _call(state, call_id)
    if call.get("facts") is not None:
        return
    if not any(t["who"] == "supplier" for t in call["transcript"]):
        procure.update(lambda s: _call(s, call_id).update(status="no answer", facts={}))
        procure.tell_user(f"{call['supplier_name']} did not answer. I can try another supplier.")
        return
    try:
        facts, _ = llm.chat_json([{"role": "user", "content": FACTS_PROMPT.format(transcript=_transcript(call))}], config.MODEL_EXTRACT, props={"stage": "call_facts"})
    except llm.LLMUnavailable:
        facts = {"summary": "Call finished, facts not extracted (model offline)."}

    def apply(s):
        _call(s, call_id).update(status="done", facts=facts)
        sup = next(x for x in s["suppliers"] if x["id"] == call["supplier_id"])
        sup.update(price=facts.get("unit_price"), currency=facts.get("currency"), in_stock=facts.get("in_stock"), lead_time_days=facts.get("lead_time_days"), documents=facts.get("documents"), source_of_terms="demo call")
        sup["unknown"] = [k for k, v in (("price", facts.get("unit_price")), ("stock", facts.get("in_stock")), ("lead time", facts.get("lead_time_days")), ("export documents", facts.get("documents"))) if v is None]

    procure.update(apply)
    analytics.capture("supplier_call_finished", {"turns": len(call["transcript"]), "got_price": facts.get("unit_price") is not None, "got_lead_time": facts.get("lead_time_days") is not None})
    procure.present_offer(call_id)


def resume(call_id: str, instruction: str) -> None:
    """Goes back to the same supplier with a counter from the coordinator."""
    procure.update(lambda s: _call(s, call_id).update(status="talking", facts=None, simulated=True, instructions=(_call(s, call_id).get("instructions") or []) + [instruction]))
    turn(call_id, None)


def notify_supplier(call_id: str, text: str) -> None:
    def add(s):
        call = _call(s, call_id)
        call["transcript"].append({"who": "agent", "text": text})

    procure.update(add)


def twiml(call_id: str, reply: dict) -> str:
    from twilio.twiml.voice_response import Gather, VoiceResponse

    response = VoiceResponse()
    if reply["done"]:
        response.say(reply["say"], voice="Polly.Amy-Neural")
        response.hangup()
        return str(response)
    gather = Gather(input="speech", action=f"{config.PUBLIC_URL}/voice/turn?call_id={call_id}", method="POST", speech_timeout="auto", language="en-GB")
    gather.say(reply["say"], voice="Polly.Amy-Neural")
    response.append(gather)
    response.redirect(f"{config.PUBLIC_URL}/voice/turn?call_id={call_id}", method="POST")
    return str(response)

