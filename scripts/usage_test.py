"""Milestone 1 check 6: how much of the usage limit does a 20-message chat use?

Run:  .venv\\Scripts\\python scripts\\usage_test.py [--model sonnet] [--web]

The SDK reports exact tokens per turn but (on this plan) not a "% of limit" figure.
So: note the usage meter at claude.ai > Settings > Usage before the run, run this,
then read it again. The report is written to data/usage_report.md.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hub.auth_check import scrub_billing_env  # noqa: E402
from hub.chat.claude_session import ChatSession  # noqa: E402

# 20 ordinary chat messages, with follow-ups so context grows like a real chat.
MESSAGES = [
    "Hi! I'm planning a weekend trip to Munich. What are three things I shouldn't miss?",
    "Which of those is best on a rainy day?",
    "How long should I plan for it?",
    "Suggest a traditional Bavarian dish I should try nearby.",
    "Is it vegetarian? If not, give me a vegetarian alternative.",
    "Switching topic: explain what a Python dataclass is, briefly.",
    "Show a tiny example with two fields.",
    "How do I make one field optional with a default?",
    "What is the difference between that and a NamedTuple?",
    "Which would you pick for a config object, and why?",
    "Give me a 3-line summary of how SQLite handles concurrent writes.",
    "So is it OK for a single-user desktop app?",
    "What journal mode should I use?",
    "Write a haiku about debugging at 2am.",
    "Now make it funnier.",
    "What's a good name for a personal automation hub app? Give 5 ideas.",
    "Which of those is your favourite?",
    "Go back to the Munich trip: what's the best way from the airport to the centre?",
    "Roughly how much does that cost?",
    "Thanks! Summarise everything we discussed in 5 bullet points.",
]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None)
    ap.add_argument("--web", action="store_true", help="keep web tools on (they add tool definitions to every turn)")
    args = ap.parse_args()

    for n in scrub_billing_env():
        print(n)

    rows, last_done, rate_events = [], None, []
    async with ChatSession(model=args.model, web_enabled=args.web) as chat:
        for i, text in enumerate(MESSAGES, 1):
            done = None
            async for ev in chat.send(text):
                if ev.kind == "done":
                    done = ev.data
                elif ev.kind == "rate_limit":
                    rate_events.append(ev.data)
                elif ev.kind == "error":
                    print(f"  error on message {i}: {ev.text}")
            if not done:
                continue
            u = done["usage"]
            rows.append((i, u.get("input_tokens", 0), u.get("cache_creation_input_tokens", 0),
                         u.get("cache_read_input_tokens", 0), u.get("output_tokens", 0), done["duration_ms"]))
            last_done = done
            print(f"{i:2d}/20  in={rows[-1][1]:>5} cache_write={rows[-1][2]:>6} cache_read={rows[-1][3]:>7} out={rows[-1][4]:>5}")
        api_key_source = chat.api_key_source

    tot = [sum(r[k] for r in rows) for k in range(1, 6)]
    model_usage = (last_done or {}).get("model_usage", {})  # cumulative for the session
    cost = (last_done or {}).get("total_cost_usd")

    lines = [
        f"# Usage test, {datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"- Messages: {len(rows)} of 20, model: {args.model or 'plan default'}, web tools: {'on' if args.web else 'off'}",
        f"- Credential source: `{api_key_source}` (`none` = subscription login, nothing billed to an API account)",
        f"- API-equivalent value (for comparison only, not charged): ${cost:.4f}" if cost is not None else "- API-equivalent value: n/a",
        "",
        "| Totals | Tokens |",
        "| --- | ---: |",
        f"| Fresh input | {tot[0]:,} |",
        f"| Cache write | {tot[1]:,} |",
        f"| Cache read | {tot[2]:,} |",
        f"| Output | {tot[3]:,} |",
        f"| Avg seconds per reply | {tot[4] / max(len(rows), 1) / 1000:.1f} |",
        "",
        f"Per-model (cumulative): `{model_usage}`",
        f"Rate-limit events: `{rate_events}`",
        "",
        "## Per message",
        "",
        "| # | in | cache write | cache read | out | s |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
        *[f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]} | {r[5] / 1000:.1f} |" for r in rows],
        "",
        "Fill in from claude.ai > Settings > Usage:  before ____ %   after ____ %",
    ]
    out = ROOT / "data" / "usage_report.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nReport written to {out}")


if __name__ == "__main__":
    asyncio.run(main())
