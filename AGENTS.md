# Treasure Map: shared agent instructions

Read by Codex (AGENTS.md) and Claude Code (CLAUDE.md imports this file). Keep rules here, not in tool-specific files.

## Context

- Hackathon: "AI Agents for the Rest of Us", AI Tinkerers Prague, 2026-11-13. Live demo at 18:00, teams of 2 to 4.
- Team: πrates (Pavlo Myrskyi, Yurii Boiko).
- Sponsors we must use well: Apify (scraping), Featherless (open-model inference, OpenAI-compatible), PostHog (analytics, LLM analytics, self-improving loop).
- Current concept and open decisions: `docs/idea.md`. Read it before starting any task.
- Private team context lives in `private/` (a separate private repo, gitignored here). If it exists, read `private/README.md` too.

## Public vs private

- This repo will be public. Internal strategy, interview notes, credits, recordings and anything about specific people goes to `private/`, never to this repo.
- `private/` is its own git repo: commit and push there separately (`git -C private ...`).

## How we work

- Before starting: `git pull`. After a meaningful step: small commit, push to `main`. No PRs today.
- Each person owns folders (see `docs/plan.md`). Do not edit another owner's folder without asking.
- Any decision (scope, stack, vertical, cut feature) goes to `docs/decisions.md` as one line: time, decision, why.
- Task status lives in `docs/plan.md`. Update it when you start or finish a task.
- Secrets only in local `.env` (see `.env.example`). Never commit keys.
- Big scraped data goes to `data/raw/` (gitignored). Small curated demo datasets can go to `data/`.

## Code style

- Simple and demo-first: working end-to-end beats clean. Always keep a cached fallback for anything that hits the network during the demo.
- No comments for self-explanatory code. No hardcoded per-case special-casing in shared code.
- Markdown: one line per bullet, no em dash character, no arrow character.
