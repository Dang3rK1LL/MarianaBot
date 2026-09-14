"""Explicit, small live check. Never used by tests/CI or called automatically."""

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from marianabot.clients import ClientError, CodexAccount, NativeClient, claude_account
from marianabot.config import Config
from marianabot.limits import SubscriptionLimits
from marianabot.store import Store


class SmokeClient(NativeClient):
    def args(self, search=False):
        args = super().args(False)
        if self.provider == "anthropic":
            args += [
                "--max-budget-usd",
                "0.10",
                "--max-turns",
                "1",
                "--system-prompt",
                "You are a connectivity test. Return the requested JSON only.",
            ]
        else:
            args[-1:-1] = ["--disable", "unbounded_connection_retries"]
        return args


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-credit-usage",
        action="store_true",
        help="Explicitly allow this test when provider usage credits are enabled",
    )
    parser.add_argument("--provider", choices=("both", "openai", "anthropic"), default="both")
    args = parser.parse_args()
    if not args.allow_credit_usage:
        parser.error(
            "This live script requires --allow-credit-usage; it may consume subscription/usage credits."
        )
    config = Config()
    config.rb.effort = config.jb.effort = "low"
    config.research.request_timeout_seconds = 120
    os.environ["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = "256"
    os.environ["CLAUDE_CODE_MAX_RETRIES"] = "0"
    store = Store(Path(".mariana"))
    workspace = store.directory / "client-workspace"
    workspace.mkdir(exist_ok=True)
    results = {}
    try:
        account = await CodexAccount(config, workspace).snapshot(include_models=True)
        await claude_account(config, workspace)
        if config.rb.model not in account["models"]:
            raise ClientError("Astra is not available; no fallback selected")
        providers = ("openai", "anthropic") if args.provider == "both" else (args.provider,)
        for provider in providers:
            limits = SubscriptionLimits(store, provider, config.subscription)
            if provider == "openai":
                limits.codex(account["limits"])
            if limits.remaining():
                results[provider] = {
                    "status": "skipped",
                    "reason": "Reported subscription cooldown",
                }
                continue
            print(f"Testing {provider}: one short request, no tools or retries.", flush=True)
            client = SmokeClient(provider, config, workspace, limits)
            try:
                result = await client.complete('Reply with exactly this JSON object: {"ok":true}')
                parsed = json.loads(result["text"])
                results[provider] = {
                    "status": "passed" if parsed == {"ok": True} else "unexpected output",
                    "model": result["model"],
                    "usage": result["usage"],
                    "api_equivalent_usd": result.get("api_equivalent_usd"),
                    "response": result["text"],
                }
            except (ClientError, ValueError, OSError) as exc:
                results[provider] = {"status": "failed", "reason": str(exc)}
            print(json.dumps({provider: results[provider]}), flush=True)
        target = store.directory / (
            "live-smoke-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + ".json"
        )
        target.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"Saved smoke-test record: {target}", flush=True)
    finally:
        store.close()
    return 0 if results and all(r["status"] == "passed" for r in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
