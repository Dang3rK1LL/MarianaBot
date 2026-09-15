"""Offline visual review: python scripts/capture_chat.py (optional: pip install resvg-py)."""

import asyncio
import os
import tempfile
from pathlib import Path

from marianabot.chat import Composer, MarianaChat
from marianabot.config import Config
from marianabot.engine import Engine
from marianabot.store import Store


async def main():
    os.environ.pop("NO_COLOR", None)  # Capture the default color UI, regardless of CI logging mode.
    output = Path(".mariana/visual-review").resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mariana-visual-") as temp:
        state = Path(temp)
        app = MarianaChat(state, state / "mariana.toml", demo=True)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
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
