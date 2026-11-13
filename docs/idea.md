# Idea: Treasure Map

Status: still brainstorming. This is the leading candidate, not locked.

## One-liner

An AI scout that maps a small business's local competitors and shows where the money is, and proves it is right by checking its own past predictions.

## Who it's for

- A sole trader (OSVČ) running a local service business, e.g. a barber in Prague 1 or 2.
- Broader audience: independent stores, freelancers, craftspeople, sole traders.

## Insight

- Owners don't read competitors' reviews (no time), and they don't act on AI advice because they can't tell if it's right.
- Existing tools (Local Falcon, LocalSpy AI, Apify's competitor analysis add-on) hand you a report and ask you to trust it.

## How it works

1. Apify Google Maps scraper pulls nearby competitors: rating, review count, opening hours, review text with dates.
2. A small Featherless model tags each review (theme, sentiment, language).
3. The scout (rules in a markdown "skill" file) writes treasures as checkable claims with evidence (review IDs).
4. Time travel: the scout only sees reviews before a cutoff (e.g. Nov 2025) and predicts trends; plain code scores predictions against reviews after the cutoff.
5. Reflection: an LLM reads the misses and rewrites the scout's rules. Versions v1, v2, v3 with accuracy tracked in PostHog. Tune on one district, report on another (holdout).
6. Best version runs on today's data: a map with 3 X marks, each with a track record ("right 7 of 10 times here last year").
7. Optional: owner taps gold or rock per treasure, scout memory learns their constraints.

## Sponsor usage

- Apify: Google Maps scraper, reviews with dates are the core data.
- Featherless: small model for tagging, larger model for predictions and reflection.
- PostHog: LLM analytics traces, accuracy per scout version, feedback events, the self-improving loop from their own scout design.

## Demo (3 min)

1. Real barber's quote: "Who has time to read competitors' reviews?"
2. Enter address, map fills with competitor pins (cached fallback).
3. "We sent our scout back to Nov 2025": v1 predictions, pins turn green or red, score 4/10.
4. It rewrites its own rules (show diff), v3 scores better, PostHog chart.
5. Today's map: 3 X marks with track record. "A map that checks itself."

## Open decisions

- Vertical: barbers vs cafés or other (decide after the data check and owner interviews).
- Delivery channel shown in demo: web map plus mock Telegram/WhatsApp message.
