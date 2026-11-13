"""Scores a rules version against the labelled offers. All scoring is plain code.

Usage:
  python -m agent.evaluate v1            score one version
  python -m agent.evaluate --improve 3   score v1, reflect, score v2, ... up to v3
"""

import json
import sys
from concurrent.futures import ThreadPoolExecutor

from . import analytics, compare, config, extract, rules


def _same(field: str, predicted, gold) -> bool:
    if predicted is None or gold is None:
        return predicted is None and gold is None
    if isinstance(gold, bool) or isinstance(gold, str):
        return predicted == gold
    return abs(predicted - gold) <= max(0.01 * abs(gold), 0.5)


def _score_split(offers: list[dict], mission: dict) -> dict:
    correct = total = caught = expected = false_alarms = 0
    misses = []
    rounds: dict[str, list] = {}
    for o in offers:
        for field in extract.FIELDS:
            total += 1
            if _same(field, o["predicted"].get(field), o["gold"].get(field)):
                correct += 1
            else:
                misses.append({"offer_id": o["id"], "field": field, "predicted": o["predicted"].get(field), "expected": o["gold"].get(field)})
        gold_traps = set(compare.traps(o["gold"], mission))
        predicted_traps = set(compare.traps(o["predicted"], mission))
        expected += len(gold_traps)
        caught += len(gold_traps & predicted_traps)
        false_alarms += len(predicted_traps - gold_traps)
        rounds.setdefault(o["round"], []).append(o)

    picks_right = 0
    for members in rounds.values():
        gold_pick = compare.recommend([{"id": m["id"], "carrier": m["carrier"], "assessment": compare.assess(m["gold"], mission)} for m in members])
        predicted_pick = compare.recommend([{"id": m["id"], "carrier": m["carrier"], "assessment": compare.assess(m["predicted"], mission)} for m in members])
        picks_right += gold_pick["offer_id"] == predicted_pick["offer_id"]

    return {
        "n_offers": len(offers),
        "field_accuracy": round(correct / total, 4),
        "trap_recall": round(caught / expected, 4) if expected else 1.0,
        "false_alarms": false_alarms,
        "pick_accuracy": round(picks_right / len(rounds), 4),
        "misses": misses,
    }


def _predict(learned: str, label: str, round_ids: list[str] | None = None) -> list[dict]:
    mission = config.load_mission()
    offers = [o for o in config.load_dataset()["offers"] if round_ids is None or o["round"] in round_ids]

    def predict(offer):
        terms = extract.extract(offer["text"], mission, learned, [], props={"rules_version": label, "stage": "eval", "offer_id": offer["id"]})
        return offer | {"predicted": terms}

    with ThreadPoolExecutor(max_workers=config.EVAL_WORKERS) as pool:
        return list(pool.map(predict, offers))


def quality(scored: dict) -> float:
    return round(scored["field_accuracy"] + scored["trap_recall"] + scored["pick_accuracy"] - 0.01 * scored["false_alarms"], 4)


def score_tune(learned: str, label: str) -> dict:
    """Scores a rule text on the tune split only. Used to accept or reject candidate rules."""
    round_ids = config.load_dataset()["splits"]["tune"]
    return _score_split(_predict(learned, label, round_ids), config.load_mission())


def run(version: str) -> dict:
    mission = config.load_mission()
    dataset = config.load_dataset()
    predicted = _predict(rules.load(version), version)

    result = {"version": version, "model": config.MODEL_EXTRACT, "splits": {}}
    for split, round_ids in dataset["splits"].items():
        scored = _score_split([o for o in predicted if o["round"] in round_ids], mission)
        result["splits"][split] = scored
        analytics.capture(
            "eval_run",
            {"rules_version": version, "split": split, "model": config.MODEL_EXTRACT, "quality": quality(scored)}
            | {k: v for k, v in scored.items() if k != "misses"},
        )
    (config.EVAL_DIR / f"{version}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    analytics.flush()
    return result


def summary() -> list[dict]:
    rows = []
    for version in rules.versions():
        path = config.EVAL_DIR / f"{version}.json"
        if not path.exists():
            continue
        result = json.loads(path.read_text())
        rows.append({"version": version, "model": result["model"]} | {
            split: {k: v for k, v in scored.items() if k != "misses"} for split, scored in result["splits"].items()
        })
    return rows


def _print(result: dict) -> None:
    for split, s in result["splits"].items():
        print(
            f"{result['version']} {split:8} fields {s['field_accuracy']:.0%}  traps {s['trap_recall']:.0%}  "
            f"false alarms {s['false_alarms']}  picks {s['pick_accuracy']:.0%}  (n={s['n_offers']})"
        )


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "--improve":
        from . import reflect

        last = f"v{args[1] if len(args) > 1 else 3}"
        version = "v1"
        while True:
            result = run(version)
            _print(result)
            if version == last:
                break
            outcome = reflect.reflect(version)
            version = outcome["version"]
            print(f"{version}: {outcome['note']}")
    else:
        _print(run(args[0] if args else rules.latest()))


if __name__ == "__main__":
    main()
