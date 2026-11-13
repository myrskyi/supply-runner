# Supply Runner

An AI agent in Telegram that helps volunteers buy aid supplies in the EU for Ukraine. It finds suppliers, checks the paperwork, gets a price, and places the order. After delivery you rate the supplier, so next time it picks better.

Built by team πrates (Pavlo Myrskyi, Yurii Boiko) at "AI Agents for the Rest of Us", AI Tinkerers Prague, 2026-11-13.

## Why

Aid loses money to overhead, delays and bad suppliers. A volunteer coordinator buying in Czechia spends days finding shops, asking for prices in Czech, and then learns at the border that a thermal imager is a dual-use item that needs an export licence.

## How it works

1. The coordinator writes in Telegram what they need, where to, and by when.
2. The agent finds real suppliers near Prague on Google Maps (Apify) and ranks them by rating, reviews, reachability and our own past orders.
3. It checks the export and import paperwork for the exact goods (EU dual-use rules, Ukrainian humanitarian aid rules) and flags the longest lead time.
4. It asks the chosen supplier for a quote in chat and brings the offer back. The coordinator accepts or sends a counter.
5. It asks only for the documents this shipment legally needs, plus payment confirmation, ticks them off, and confirms the order to both sides with a PDF.
6. After delivery it asks for a 1 to 5 rating. The rating changes the supplier ranking next time.

## Sponsors

- **Apify**: Google Maps scraping for real suppliers, cached for reliability.
- **Featherless**: open models (Qwen3 30B) for reading the request, the supplier chat, offer extraction and document matching.
- **PostHog**: every step is an event (request, search, compliance check, quote, offer, order, rating) plus LLM analytics.

## Run it

```bash
cp .env.example .env
uv sync
uv run uvicorn agent.server:app --port 8000
```

- Fill in `FEATHERLESS_API_KEY`, `APIFY_TOKEN`, `POSTHOG_API_KEY` and `TELEGRAM_BOT_TOKEN` in `.env`. Never commit it.
- Send `/start` to your bot, then a request.
- Presentation view: `http://localhost:8000/stage` (the supplier replies are typed there).
- Operator view with a built-in chat: `http://localhost:8000/`.
- Specimen documents to attach in Telegram: `data/documents/` (regenerate with `uv run python -m agent.paperwork`).

## Code

- `agent/procure.py`: request parsing, supplier search and ranking, compliance, offer, documents, order, feedback.
- `agent/voice.py`: supplier conversation (written chat, or a Twilio phone call when voice is configured).
- `agent/telegram.py`: Telegram bot via long polling, buttons, file sending.
- `agent/paperwork.py`: specimen PDFs, purchase order and order confirmation.
- `data/procure/compliance.json`: curated rules with sources and lead times.
- `web/stage.html`: one-screen presentation view.

## Honest notes

- In the demo a team member plays the supplier. Prices are not the real shop's.
- The past orders in `data/procure/history_seed.json` are constructed for the demo, not real experiences with these shops.
- The documents in `data/documents/` are fictional and marked SPECIMEN.
- The compliance check is a curated checklist with sources, not legal advice.
