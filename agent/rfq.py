from . import config, llm

TEMPLATE = """Dobrý den,

poptáváme přepravu humanitární pomoci:
- Trasa: {origin} – {destination}
- Náklad: {cargo}, {weight} kg, {pallets} palety
- Doručení nejpozději: {deadline}

Prosíme o nabídku s těmito údaji:
1. Celková cena včetně DPH a všech příplatků
2. Zda je v ceně pojištění nákladu (nad rámec odpovědnosti dle CMR)
3. Zda je v ceně vykládka u příjemce (ve skladu není vysokozdižný vozík)
4. Datum vyzvednutí a doručení
5. Platnost nabídky

Děkujeme,
koordinační tým mise

---
Hello, we are requesting a quote for humanitarian cargo: {origin} to {destination}, {weight} kg on {pallets} pallets, delivery by {deadline}. Please state the total price incl. VAT and surcharges, whether cargo insurance and unloading are included, pickup and delivery dates, and how long the offer is valid."""

PROMPT = """Write a short request for quotation email to a freight carrier, first in Czech, then the same in English after a line with "---".
Mission: {cargo}, {weight} kg on {pallets} pallets, from {origin} to {destination}, delivery on or before {deadline}.
Ask explicitly for: total price including VAT and all surcharges, whether cargo insurance beyond CMR liability is included, whether unloading at destination is included (no forklift there), pickup and delivery dates, offer validity.
Use a numbered list for the questions. Sign as "koordinační tým mise". No subject line, no placeholders."""


def _values(mission: dict) -> dict:
    return {
        "cargo": mission["cargo"],
        "weight": mission["weight_kg"],
        "pallets": mission["pallets"],
        "origin": mission["origin"]["city"],
        "destination": mission["destination"]["city"],
        "deadline": mission["deadline"],
    }


def draft(mission: dict) -> dict:
    values = _values(mission)
    try:
        answer = llm.chat([{"role": "user", "content": PROMPT.format(**values)}], config.MODEL_EXTRACT, props={"stage": "rfq"}, max_tokens=900)
        return {"text": answer["text"], "source": "cache" if answer["cached"] else "model"}
    except llm.LLMUnavailable:
        return {"text": TEMPLATE.format(**values), "source": "template"}
