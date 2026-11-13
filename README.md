# Supply Runner

An AI agent in Telegram that helps volunteers buy aid supplies in the EU for Ukraine. It finds suppliers, checks the paperwork, gets a price, and places the order. After delivery you rate the supplier, so next time it picks better.

Built by team πrates (Pavlo Myrskyi, Yurii Boiko) at "AI Agents for the Rest of Us", AI Tinkerers Prague, 2026-11-13.

> **Demo build.** This repo is a one-day hackathon prototype made to show the idea. The real product would look different: see [From demo to product](#from-demo-to-product).

## Try it

**Live demo: [supply-runner.onrender.com](https://supply-runner.onrender.com)** (free hosting, the first load after a pause can take up to a minute).

You play both sides: the volunteer chat on the left and the shop on the agent screen on the right. Every visitor gets a private session. The whole run takes about 2 minutes:

1. Left, click **Use example request** (20 first aid kits and 2 thermal imagers to Kharkiv). The agent ranks real shops near Prague, excludes one with a bad past order, and lists the paperwork, led by a 30-day dual-use export licence.
2. Left, click **💬 Ask C.I.D Praha s.r.o. for a quote**. The agent writes to the shop on the right.
3. Right, you are the shop now: click **Example**, then **Send**. The agent reads the reply and brings an offer back to the left.
4. Left, click **✅ Accept offer**. The agent asks for the documents this shipment legally needs and sends a purchase order PDF.
5. Left, click **📎 Attach demo documents**. Each document ticks off, the order is confirmed with a PDF.
6. Left, rate the shop **1 to 5 ⭐**. The rating changes the ranking for the next search.

Use **start over** to run it again. You can type your own request or a counter offer (for example `max 20000 CZK, pickup tomorrow`) instead of clicking.

Prefer Telegram? Message [@pirates_supply_runner_bot](https://t.me/pirates_supply_runner_bot) with a request. When the agent asks for a quote, the bot sends you a link to your shop page, and you can attach the PDFs from `data/documents/`.

The shop is played by you and prices are not real. Past orders are seeded and the documents are specimen PDFs.

## Why

Aid loses money to overhead, delays and bad suppliers. A volunteer coordinator buying in Czechia spends days finding shops, asking for prices in Czech, and then learns at the border that a thermal imager is a dual-use item that needs an export licence.

## How it works

1. The coordinator writes in Telegram what they need, where to, and by when.
2. The agent finds real suppliers near Prague on Google Maps (Apify) and ranks them by rating, reviews, reachability and our own past orders.
3. It checks the export and import paperwork for the exact goods (EU dual-use rules, Ukrainian humanitarian aid rules) and flags the longest lead time.
4. It asks the chosen supplier for a quote in chat and brings the offer back. The coordinator accepts or sends a counter.
5. It asks only for the documents this shipment legally needs, plus payment confirmation, ticks them off, and confirms the order to both sides with a PDF.
6. After delivery it asks for a 1 to 5 rating. The rating changes the supplier ranking next time.

## From demo to product

What changes for real coordinators:

- **Channels:** WhatsApp Business API with a verified number and approved templates, email as the main quote channel (written trail), voice calls to real supplier numbers in Czech.
- **Calls:** the agent says it is an AI (EU AI Act) and records only with consent. Low-latency streaming speech.
- **Suppliers:** a search per item instead of fixed categories (Google Maps, e-shops, distributor catalogues), prices scraped where public, company check in ARES and EU sanctions lists.
- **Compliance:** rules in a versioned database with source, review date and owner, checked by a customs broker. The model proposes a control number per exact model, a human confirms. The model only picks from curated rules, unclear cases say "verify".
- **Safety:** the agent never buys or pays on its own, the coordinator confirms every order. Rate limits, an audit log, supplier opt-out. Supplier replies are treated as data, never as instructions.
- **Infrastructure:** Postgres instead of JSON files, a job queue for searches and follow-ups that take days, per-organisation accounts and roles, a secrets manager, GDPR retention for supplier data and recordings.
- **Quality:** PostHog tracks quote success rate, extraction accuracy against manual checks, and time from request to checklist. Coordinator corrections become new rules and test cases.

## Next steps

1. Email channel for quotes.
2. Real supplier contacts with an AI disclosure.
3. A compliance checklist reviewed by a customs broker.
4. Postgres and accounts, then a pilot with one volunteer group.

## Run it yourself

```bash
cp .env.example .env
uv sync
uv run uvicorn agent.server:app --port 8000
```

- Fill in `FEATHERLESS_API_KEY`, `APIFY_TOKEN`, `POSTHOG_API_KEY` and `TELEGRAM_BOT_TOKEN` in `.env`. Never commit it.
- Play page: `http://localhost:8000/`. Presentation view: `/stage?s=<session>`. Older operator view: `/operator`.
- Each web visitor and each Telegram chat has its own state in `data/sessions/<id>/`.
- Specimen documents to attach in Telegram: `data/documents/` (regenerate with `uv run python -m agent.paperwork`).

## Deploy

- `Dockerfile` and `render.yaml` are ready for a Render free web service (Blueprint from this repo).
- Set `FEATHERLESS_API_KEY`, `APIFY_TOKEN`, `POSTHOG_API_KEY`, `TELEGRAM_BOT_TOKEN` and `PUBLIC_URL` (the service URL) in the Render dashboard.
- Only one process may poll a Telegram bot, so stop any local server using the same token.
- Supplier search falls back to the committed snapshots in `data/procure/suppliers_*.json` when the Apify cache is empty.

## Code

- `agent/procure.py`: request parsing, supplier search and ranking, compliance, offer, documents, order, feedback.
- `agent/voice.py`: supplier conversation (written chat, or a Twilio phone call when voice is configured).
- `agent/telegram.py`: Telegram bot via long polling, buttons, file sending.
- `agent/paperwork.py`: specimen PDFs, purchase order and order confirmation.
- `data/procure/compliance.json`: curated rules with sources and lead times.
- `web/play.html`: public play page (volunteer chat plus the agent screen).
- `web/stage.html`: one-screen agent view, where you also answer as the shop.
