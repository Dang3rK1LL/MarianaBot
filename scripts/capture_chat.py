"""Offline visual review: python scripts/capture_chat.py (optional: pip install resvg-py)."""

import asyncio
import os
import tempfile
import time
from pathlib import Path

from marianabot.chat import Composer, MarianaChat
from marianabot.config import Config
from marianabot.engine import Engine
from marianabot.store import Store
from marianabot.usage import normalize_usage


async def main():
    os.environ.pop("NO_COLOR", None)  # Capture the default color UI, regardless of CI logging mode.
    output = Path(".mariana/visual-review").resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mariana-visual-") as temp:
        state = Path(temp)
        app = MarianaChat(state, state / "mariana.toml", demo=True)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            app.save_screenshot("welcome.svg", path=str(output))
            app.query_one(Composer).load_text("/")
            await pilot.pause()
            app.save_screenshot("commands.svg", path=str(output))
            await pilot.resize_terminal(80, 24)
            await pilot.pause()
            app.save_screenshot("narrow.svg", path=str(output))
            await pilot.press("f1")
            await pilot.pause(1)
            app.save_screenshot("help.svg", path=str(output))
        store = Store(state)
        config = Config()
        config.research.max_rounds = 2
        config.research.min_rounds = 1
        run_id = store.create_run(
            "Develop a 30-day pilot for a local business subscription service. Budget: EUR 2,000. Time: 10 hours per week.",
            config,
            demo=True,
        )
        await Engine(store, run_id).run()
        store.close()
        app = MarianaChat(state, state / "mariana.toml", demo=True, run_id=run_id)
        async with app.run_test(size=(120, 40)) as pilot:
            app.action_latest()
            await pilot.pause()
            app.save_screenshot("conversation.svg", path=str(output))
        # Synthetic account reports exercise the working layout without contacting providers.
        fixture = state / "usage-fixture"
        store = Store(fixture)
        run_id = store.create_run("Usage display fixture. No model calls were made.", config)
        store.update_run(run_id, status="running", round=2)
        store.message(
            run_id,
            "plan",
            "RB",
            "Round 2 · Research plan",
            "### Test demand before committing the budget\n\nRecruit ten local businesses for interviews this week. Offer three paid pilots before building the full service.\n\n**Open question:** will buyers pay enough to cover delivery time?\n\nJB is checking the pricing assumptions and the cost of acquiring customers.",
        )
        now = time.time()
        for provider, brain, incoming, outgoing in (
            ("openai", "RB", 142680, 9241),
            ("anthropic", "JB", 98310, 6142),
        ):
            call_id = store.begin_call(run_id, "fixture-saved", brain, provider, "fixture")
            store.finish(
                call_id, "done", {"usage": {"input_tokens": incoming, "output_tokens": outgoing}}
            )
            store.set_limits(
                provider,
                {
                    "observed": now,
                    "windows": [
                        {
                            "name": "five_hour",
                            "percent": 42 if provider == "openai" else 31,
                            "reset": now + 9180,
                            "observed": now,
                        },
                        {
                            "name": "seven_day",
                            "percent": 16 if provider == "openai" else 12,
                            "reset": now + 180000,
                            "observed": now,
                        },
                    ],
                },
            )
        call_id = store.begin_call(run_id, "fixture-active", "JB", "anthropic", "fixture")
        store.record_usage(
            call_id, normalize_usage("anthropic", {"input_tokens": 3280, "output_tokens": 480})
        )
        store.close()

        class PreviewManager:
            def active(self):
                return {"run_id": run_id, "mode": "research"}

        app = MarianaChat(fixture, state / "mariana.toml", run_id=run_id, manager=PreviewManager())
        async with app.run_test(size=(120, 32)) as pilot:
            await pilot.pause(1)
            app.save_screenshot("usage.svg", path=str(output))
            await pilot.resize_terminal(80, 24)
            await pilot.pause(1)
            app.save_screenshot("usage-narrow.svg", path=str(output))
    try:
        import resvg_py
    except ImportError:
        print("SVG screenshots saved. Install resvg-py to also render PNGs.")
    else:
        for path in output.glob("*.svg"):
            # Textual's SVG asks for Fira Code; use the same-width Windows console font.
            svg = path.read_text(encoding="utf-8").replace("Fira Code", "Consolas")
            path.with_suffix(".png").write_bytes(
                resvg_py.svg_to_bytes(svg_string=svg, font_family="Consolas")
            )
    print(output)


if __name__ == "__main__":
    asyncio.run(main())
