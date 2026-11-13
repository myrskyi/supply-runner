from datetime import date

from . import config, llm

FIELDS = [
    "price",
    "currency",
    "vat_included",
    "insurance_included",
    "unloading_included",
    "min_charge",
    "surcharge_total",
    "delivery_date",
    "valid_until",
    "insurance_fee",
    "unloading_fee",
]

SYSTEM = (
    "You extract the terms of a transport offer for a humanitarian mission coordinator. "
    "Never invent values. Use null when the offer does not state something."
)

SCHEMA = """Return one JSON object with exactly these keys:
- price: number or null. Transport price for this shipment, in the offer currency.
- currency: "CZK", "EUR" or null.
- vat_included: true, false or null.
- insurance_included: true, false or null.
- unloading_included: true, false or null.
- min_charge: number or null.
- surcharge_total: number. Extra fees for this shipment, 0 if none.
- delivery_date: "YYYY-MM-DD" or null.
- valid_until: "YYYY-MM-DD" or null.
- insurance_fee: number or null. Fee to add cargo insurance when it is not included.
- unloading_fee: number or null. Fee to add unloading when it is not included.
- caveats: array of short English strings. Conditions that may add cost or risk.
- blocking_issues: array of short English strings. Only clear violations of the COORDINATOR RULES below, otherwise empty."""


def build_messages(text: str, mission: dict, learned_rules: str, coordinator_rules: list[str]) -> list:
    coordinator = "\n".join(f"- {r}" for r in coordinator_rules) or "(none)"
    user = f"""MISSION
Cargo: {mission['cargo']}, {mission['weight_kg']} kg on {mission['pallets']} pallets.
Route: {mission['origin']['city']} to {mission['destination']['city']}.
Today: {mission['today']}. Delivery deadline: {mission['deadline']}.

{SCHEMA}

EXTRACTION RULES
{learned_rules or '(none)'}

COORDINATOR RULES
{coordinator}

OFFER
{text}

Return only the JSON object."""
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(str(value).replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def _boolean(value):
    return value if isinstance(value, bool) else None


def _iso_date(value):
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        return None


def _strings(value):
    return [str(v) for v in value if v] if isinstance(value, list) else []


def normalize(raw: dict) -> dict:
    currency = str(raw.get("currency") or "").upper()
    return {
        "price": _number(raw.get("price")),
        "currency": currency if currency in ("CZK", "EUR") else None,
        "vat_included": _boolean(raw.get("vat_included")),
        "insurance_included": _boolean(raw.get("insurance_included")),
        "unloading_included": _boolean(raw.get("unloading_included")),
        "min_charge": _number(raw.get("min_charge")),
        "surcharge_total": _number(raw.get("surcharge_total")) or 0,
        "delivery_date": _iso_date(raw.get("delivery_date")),
        "valid_until": _iso_date(raw.get("valid_until")),
        "insurance_fee": _number(raw.get("insurance_fee")),
        "unloading_fee": _number(raw.get("unloading_fee")),
        "caveats": _strings(raw.get("caveats")),
        "blocking_issues": _strings(raw.get("blocking_issues")),
    }


def _offline(text: str) -> dict:
    for offer in config.load_dataset()["offers"]:
        if offer["text"].strip() == text.strip():
            return normalize(offer["gold"]) | {"source": "fixture", "seconds": 0.0}
    empty = normalize({})
    empty["caveats"] = ["Model offline: terms were not extracted"]
    return empty | {"source": "offline", "seconds": 0.0}


def extract(text: str, mission: dict, learned_rules: str, coordinator_rules: list[str], *, props: dict | None = None, allow_offline: bool = False) -> dict:
    messages = build_messages(text, mission, learned_rules, coordinator_rules)
    try:
        raw, meta = llm.chat_json(messages, config.MODEL_EXTRACT, props=props)
    except llm.LLMUnavailable:
        if allow_offline:
            return _offline(text)
        raise
    return normalize(raw) | {"source": "cache" if meta["cached"] else "model", "seconds": meta["seconds"]}
