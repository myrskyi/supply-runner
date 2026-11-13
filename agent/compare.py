FAIL_LABELS = {
    "no_insurance": "no cargo insurance",
    "no_unloading": "unloading not included",
    "expired": "offer already expired",
    "late": "delivery after the deadline",
    "over_budget": "over budget",
}
NOTE_LABELS = {
    "vat_excluded": "price excludes VAT, added to the total",
    "min_charge_applies": "minimum charge is higher than the quoted price",
    "insurance_extra": "cargo insurance is a paid extra, added to the total",
    "unloading_extra": "unloading is a paid extra, added to the total",
}
REQUIRED_SERVICES = ("insurance", "unloading")
UNKNOWN_LABELS = {
    "price": "price",
    "vat_included": "VAT",
    "insurance_included": "insurance",
    "unloading_included": "unloading",
    "delivery_date": "delivery date",
    "valid_until": "validity",
}


def total_czk(terms: dict, mission: dict) -> float | None:
    if terms.get("price") is None or terms.get("currency") not in ("CZK", "EUR"):
        return None
    amount = max(terms["price"], terms.get("min_charge") or 0) + (terms.get("surcharge_total") or 0)
    for service in REQUIRED_SERVICES:
        if terms.get(f"{service}_included") is False:
            amount += terms.get(f"{service}_fee") or 0
    if terms.get("vat_included") is False:
        amount *= 1 + mission["vat_rate"]
    if terms["currency"] == "EUR":
        amount *= mission["eur_to_czk"]
    return round(amount, 2)


def traps(terms: dict, mission: dict) -> list[str]:
    found = []
    if terms.get("vat_included") is False:
        found.append("vat_excluded")
    for service in REQUIRED_SERVICES:
        if terms.get(f"{service}_included") is False:
            found.append(f"no_{service}" if terms.get(f"{service}_fee") is None else f"{service}_extra")
    if terms.get("min_charge") and terms.get("price") is not None and terms["min_charge"] > terms["price"]:
        found.append("min_charge_applies")
    if terms.get("valid_until") and terms["valid_until"] < mission["today"]:
        found.append("expired")
    if terms.get("delivery_date") and terms["delivery_date"] > mission["deadline"]:
        found.append("late")
    return found


def assess(terms: dict, mission: dict) -> dict:
    total = total_czk(terms, mission)
    found = traps(terms, mission)
    fails = [FAIL_LABELS[t] for t in found if t in FAIL_LABELS]
    if total is not None and total > mission["budget_czk"]:
        fails.append(FAIL_LABELS["over_budget"])
    fails += [f"coordinator rule: {issue}" for issue in terms.get("blocking_issues") or []]
    notes = [NOTE_LABELS[t] for t in found if t in NOTE_LABELS]
    unknowns = [label for key, label in UNKNOWN_LABELS.items() if terms.get(key) is None]
    return {
        "total_czk": total,
        "traps": found,
        "fails": fails,
        "notes": notes,
        "unknowns": unknowns,
        "eligible": total is not None and not fails,
    }


def recommend(offers: list[dict]) -> dict:
    """offers: [{"id", "carrier", "assessment", "rejected"}]. Cheapest eligible offer, fully confirmed ones first."""
    live = [o for o in offers if not o.get("rejected")]
    eligible = [o for o in live if o["assessment"]["eligible"]]
    eligible.sort(key=lambda o: (bool(o["assessment"]["unknowns"]), o["assessment"]["total_czk"]))
    priced = sorted((o for o in live if o["assessment"]["total_czk"] is not None), key=lambda o: o["assessment"]["total_czk"])
    cheapest = priced[0] if priced else None

    if not eligible:
        reason = "No offer meets the mission requirements yet."
        if cheapest:
            reason += f" Closest is {cheapest['carrier']}: {', '.join(cheapest['assessment']['fails']) or 'needs clarification'}."
        return {"offer_id": None, "reason": reason}

    pick = eligible[0]
    a = pick["assessment"]
    parts = []
    if cheapest and cheapest["id"] != pick["id"]:
        diff = a["total_czk"] - cheapest["assessment"]["total_czk"]
        parts.append(
            f"{cheapest['carrier']} looks cheapest at {cheapest['assessment']['total_czk']:,.0f} CZK but fails: "
            f"{', '.join(cheapest['assessment']['fails'])}."
        )
        parts.append(f"Recommended: {pick['carrier']} at {a['total_czk']:,.0f} CZK ({diff:+,.0f} CZK), meets every requirement.")
    else:
        parts.append(f"Recommended: {pick['carrier']} at {a['total_czk']:,.0f} CZK, the cheapest offer that meets every requirement.")
    if a["notes"]:
        parts.append("Note: " + "; ".join(a["notes"]) + ".")
    if a["unknowns"]:
        parts.append("Still unknown, confirm before booking: " + ", ".join(a["unknowns"]) + ".")
    return {"offer_id": pick["id"], "reason": " ".join(parts)}
