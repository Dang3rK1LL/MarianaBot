"""Offline visual review: python scripts/capture_chat.py (optional: pip install resvg-py)."""

import argparse
import asyncio
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from marianabot.chat import Composer, MarianaChat
from marianabot.config import Config, save_model_preferences
from marianabot.engine import Engine
from marianabot.model_catalog import ModelOption
from marianabot.store import Store
from marianabot.usage import normalize_usage


async def main(output: Path):
    os.environ.pop("NO_COLOR", None)  # Capture the default color UI, regardless of CI logging mode.
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mariana-visual-") as temp:
        state = Path(temp)
        app = MarianaChat(state, state / "mariana.toml", demo=True)
        async with app.run_test(size=(180, 50)) as pilot:
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
        config.rb.model, config.rb.effort = "gpt-6.1-sol", "xhigh"
        config.jb.effort = "high"
        save_model_preferences(state / "mariana.toml", config, None)
        config.research.max_rounds = 2
        config.research.min_rounds = 1
        with patch("marianabot.store.research_id", return_value="MB-7K3M-9Q2R-5V8N"):
            run_id = store.create_run(
                "Assess demand and competitors for a repair scheduling service using public online evidence. Budget: EUR 2,000. Time: 10 hours per week.",
                config,
                demo=True,
            )
        await Engine(store, run_id).run()
        store.close()
        app = MarianaChat(state, state / "mariana.toml", demo=True, run_id=run_id)
        async with app.run_test(size=(180, 50)) as pilot:
            app.action_latest()
            await pilot.pause()
            app.save_screenshot("conversation.svg", path=str(output))

            async def model_fixture(provider, config, cwd):
                if provider == "rb":
                    return [
                        ModelOption("gpt-6-luna", "GPT-6 Luna", ("low", "medium", "high")),
                        ModelOption(
                            "gpt-6-astra", "GPT-6 Astra", ("low", "medium", "high", "xhigh", "max")
                        ),
                        ModelOption(
                            "gpt-6.1-sol", "GPT-6.1 Sol", ("low", "medium", "high", "xhigh", "max")
                        ),
                    ]
                return [
                    ModelOption("claude-sonnet-5", "Sonnet 5", ("low", "medium", "high")),
                    ModelOption(
                        "claude-opus-5-5", "Opus 5.5", ("low", "medium", "high", "xhigh", "max")
                    ),
                    ModelOption("claude-haiku-4-5-20251001", "Haiku 4.5", (), "auto"),
                ]

            with patch("marianabot.models_ui.load_models", model_fixture):
                app.action_models()
                await pilot.pause(0.2)
                app.save_screenshot("models.svg", path=str(output))
                await pilot.resize_terminal(80, 24)
                await pilot.pause(0.2)
                app.save_screenshot("models-narrow.svg", path=str(output))
                await pilot.click("#rb-model")
                await pilot.pause()
                app.save_screenshot("models-dropdown.svg", path=str(output))
        # Synthetic account reports exercise the working layout without contacting providers.
        fixture = state / "usage-fixture"
        store = Store(fixture)
        with patch("marianabot.store.research_id", return_value="MB-7K3M-9Q2R-5V8N"):
            run_id = store.create_run(
                "Assess demand and competitors for a repair scheduling service using public online evidence. Budget: EUR 2,000. Time: 10 hours per week.\n\nSynthetic preview; no model calls were made.",
                config,
            )
        store.update_run(run_id, status="running", round=2)
        store.message(
            run_id,
            "plan",
            "RB",
            "Round 2 · Research plan",
            "## Round summary\nPublic listings show several established scheduling tools. Buyer reviews point to price and setup effort as recurring concerns.\n\n## Changes this round\nNarrowed the comparison to products aimed at small repair shops. Published pricing is available; willingness to pay remains unverified.\n\n## Next direction\nCheck documented prices and review patterns against the original budget. JB is reviewing the evidence and scope.\n\nSynthetic preview; no live research was performed.",
        )
        now = time.time()
        for provider, brain, incoming, outgoing in (
            ("openai", "RB", 142680, 9241),
            ("anthropic", "JB", 98310, 6142),
        ):
            call_id = store.begin_call(
                run_id,
                "fixture-saved",
                brain,
                provider,
                "gpt-6.1-sol" if brain == "RB" else "claude-sonnet-5",
                "medium",
            )
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
        call_id = store.begin_call(run_id, "fixture-mb", "MB", "openai", "gpt-6-luna", "low")
        store.finish(
            call_id,
            "done",
            {"text": "Synthetic brief", "usage": {"input_tokens": 0, "output_tokens": 0}},
        )
        call_id = store.begin_call(
            run_id, "fixture-active", "JB", "anthropic", "claude-sonnet-5", "medium"
        )
        store.record_usage(
            call_id, normalize_usage("anthropic", {"input_tokens": 3280, "output_tokens": 480})
        )
        store.close()

        class PreviewManager:
            def active(self):
                return {"run_id": run_id, "mode": "research"}

        app = MarianaChat(fixture, state / "mariana.toml", run_id=run_id, manager=PreviewManager())
        with patch.object(app, "request_usage_refresh", return_value=None):
            async with app.run_test(size=(180, 50)) as pilot:
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".mariana/visual-review"))
    asyncio.run(main(parser.parse_args().output))
