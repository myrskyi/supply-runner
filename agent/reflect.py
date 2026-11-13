"""Reads the agent's misses on the tune split and rewrites its extraction rules. Holdout offers are never shown here."""

import json

from . import analytics, config, llm, rules

SYSTEM = "You improve the instructions of an agent that extracts terms from transport offers."

CANDIDATE_TEMPERATURES = (0.0, 0.6, 0.9)

PROMPT = """The agent extracts terms from carrier offers. Its output format:
{schema}

Plain code compared its answers with the correct ones. Below are its current rules and every miss.

CURRENT RULES
{current}

MISSES
{misses}

Write a better rule list. Requirements:
- General rules only. No carrier names, and no numbers copied from these offers.
- Each rule is one line starting with "- ", in English, at most 30 words.
- Keep rules that still look right, fix or drop the ones that caused misses, add new ones for the patterns you see.
- At most 14 rules.
Return only the rule list."""


def _propose(prompt: str, version: str, temperature: float) -> str:
    answer = llm.chat(
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
        config.MODEL_REFLECT,
        props={"stage": "reflect", "from_version": version},
        temperature=temperature,
        max_tokens=1200,
    )
    return "\n".join(line.strip() for line in answer["text"].splitlines() if line.strip().startswith("- "))


def reflect(version: str) -> dict:
    """Proposes candidate rule lists from the tune misses and keeps one only if it scores better on tune."""
    from . import evaluate
    from .extract import SCHEMA

    current_rules = rules.load(version)
    current = json.loads((config.EVAL_DIR / f"{version}.json").read_text())["splits"]["tune"]
    offers = {o["id"]: o for o in config.load_dataset()["offers"]}
    by_offer: dict[str, list] = {}
    for miss in current["misses"]:
        by_offer.setdefault(miss["offer_id"], []).append(miss)

    blocks = []
    for offer_id, misses in by_offer.items():
        lines = "\n".join(f"  {m['field']}: agent said {json.dumps(m['predicted'])}, correct is {json.dumps(m['expected'])}" for m in misses)
        blocks.append(f"Offer:\n{offers[offer_id]['text']}\nMisses:\n{lines}")

    new_version = rules.next_version(version)
    best_rules, best_quality, tried = current_rules, evaluate.quality(current), []
    if blocks:
        prompt = PROMPT.format(schema=SCHEMA, current=current_rules or "(none)", misses="\n\n".join(blocks))
        for temperature in CANDIDATE_TEMPERATURES:
            candidate = _propose(prompt, version, temperature)
            if not candidate or candidate == current_rules:
                continue
            candidate_quality = evaluate.quality(evaluate.score_tune(candidate, f"{new_version}-candidate"))
            tried.append(candidate_quality)
            if candidate_quality > best_quality:
                best_rules, best_quality = candidate, candidate_quality

    accepted = best_rules != current_rules
    rules.save(new_version, best_rules)
    analytics.capture(
        "rules_rewritten",
        {
            "from_version": version,
            "to_version": new_version,
            "accepted": accepted,
            "n_candidates": len(tried),
            "candidate_quality": tried,
            "tune_quality_before": evaluate.quality(current),
            "tune_quality_after": best_quality,
            "n_misses": len(current["misses"]),
        },
    )
    note = f"tune quality {evaluate.quality(current)} to {best_quality}, candidates {tried}, " + ("accepted" if accepted else "kept old rules")
    return {"version": new_version, "accepted": accepted, "note": note}
