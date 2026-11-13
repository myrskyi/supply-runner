"""Finds carriers on Google Maps through Apify. The result is cached so the demo never waits on a live scrape."""

import json
import math
from datetime import datetime, timezone

from . import config

ACTOR = "compass/crawler-google-places"
CACHE_FILE = config.CACHE / "carriers.json"
FIXTURE_FILE = config.DATA / "fixtures" / "carriers.json"
SHORTLIST_SIZE = 6


def _get(item, key, default=None):
    return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)


def _emails(item: dict) -> list[str]:
    found = item.get("emails") or []
    return [e for e in found if isinstance(e, str)]


def _normalize(item: dict, checked_at: str) -> dict:
    location = item.get("location") or {}
    return {
        "id": item.get("placeId") or item.get("title"),
        "name": item.get("title"),
        "category": item.get("categoryName"),
        "address": item.get("address"),
        "lat": location.get("lat"),
        "lng": location.get("lng"),
        "phone": item.get("phone"),
        "website": item.get("website"),
        "emails": _emails(item),
        "rating": item.get("totalScore"),
        "reviews": item.get("reviewsCount") or 0,
        "source_url": item.get("url"),
        "source": "Google Maps via Apify",
        "checked_at": checked_at,
    }


def _score(c: dict) -> float:
    reachable = bool(c["website"]) + bool(c["phone"]) + bool(c["emails"])
    return reachable * 2 + (c["rating"] or 0) + math.log10(1 + c["reviews"])


def _unverified(c: dict) -> list[str]:
    missing = ["price", "cargo insurance", "unloading"]
    if not c["emails"]:
        missing.append("email address")
    if not c["website"]:
        missing.append("website")
    return missing


def is_carrier(c: dict, mission: dict) -> bool:
    allowed = mission["search"].get("categories")
    return not allowed or c.get("category") in allowed


def shortlist(carriers: list[dict], mission: dict) -> list[dict]:
    ranked = sorted((c for c in carriers if c["name"] and c["lat"] and is_carrier(c, mission)), key=_score, reverse=True)
    return [c | {"unverified": _unverified(c)} for c in ranked[:SHORTLIST_SIZE]]


def scrape(mission: dict, cache_file=CACHE_FILE) -> dict:
    from apify_client import ApifyClient

    s = mission["search"]
    client = ApifyClient(config.APIFY_TOKEN)
    run = client.actor(ACTOR).call(
        run_input={
            "searchStringsArray": s["queries"],
            "locationQuery": s["location"],
            "maxCrawledPlacesPerSearch": s["max_per_query"],
            "language": s["language"],
            "scrapeContacts": True,
            "skipClosedPlaces": True,
        }
    )
    dataset_id = _get(run, "defaultDatasetId") or _get(run, "default_dataset_id")
    items = client.dataset(dataset_id).list_items().items
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    seen, carriers = set(), []
    for item in items:
        c = _normalize(dict(item), checked_at)
        if c["id"] and c["id"] not in seen:
            seen.add(c["id"])
            carriers.append(c)
    result = {"source": "apify", "queries": s["queries"], "location": s["location"], "checked_at": checked_at, "carriers": carriers}
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def search(mission: dict, refresh: bool = False) -> dict:
    if CACHE_FILE.exists() and not refresh:
        return json.loads(CACHE_FILE.read_text()) | {"cached": True}
    if config.APIFY_TOKEN:
        return scrape(mission) | {"cached": False}
    return json.loads(FIXTURE_FILE.read_text()) | {"cached": True}


if __name__ == "__main__":
    mission = config.load_mission()
    found = scrape(mission)
    print(f"{len(found['carriers'])} places cached to {CACHE_FILE}, {sum(is_carrier(c, mission) for c in found['carriers'])} are carriers")
    for c in shortlist(found["carriers"], mission):
        print(f"  {c['name']} | {c['rating']} ({c['reviews']}) | {c['website']} | {c['emails']}")
