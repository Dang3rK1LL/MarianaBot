"""Review a completed snapshot and validate guided output with real provider calls.

Never creates or resumes research. This consumes subscription allowance, is never
run automatically, and writes private provider responses to --output.
"""

import argparse
import asyncio
import json
import os
import sqlite3
import time
from pathlib import Path

from marianabot.clients import ClaudeAccount, CodexAccount, NativeClient, claude_account
from marianabot.config import Config
from marianabot.guidance import (
    INTAKE,
    QUESTION_AUDIT,
    QUESTION_PROMPT,
    Intake,
    QuestionAudit,
    Questions,
    validate_questions,
)
from marianabot.limits import SubscriptionLimits
from marianabot.model_catalog import load_models
from marianabot.prompts import JUDGE, Review, prompt
from marianabot.routing import TaskRouter
from marianabot.store import Store


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True, help="Private SQLite backup")
    parser.add_argument("--run-id", required=True, help="A completed live research ID")
    parser.add_argument("--output", type=Path, required=True, help="New private results directory")
    parser.add_argument("--provider", choices=("both", "openai"), default="both")
    args = parser.parse_args()
    with sqlite3.connect(args.snapshot.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM runs WHERE id=?", (args.run_id,)).fetchone()
        if row is None or row["status"] != "complete" or row["demo"]:
            parser.error("Select an already completed, real research run")
        run = dict(row)
        rounds = [
            dict(row) | {"review": json.loads(row["review"])}
            for row in db.execute(
                "SELECT * FROM rounds WHERE run_id=? ORDER BY number", (args.run_id,)
            )
        ]
        statistics = [
            dict(row)
            for row in db.execute(
                "SELECT brain,state,count(*) AS calls FROM calls WHERE run_id=? GROUP BY brain,state",
                (args.run_id,),
            )
        ]
    if not rounds:
        parser.error("The selected research has no completed rounds")
    config = Config.model_validate_json(run["config"])
    if not config.subscription.overage_disabled:
        parser.error("Subscription-only validation requires the saved disabled-overage attestation")
    config.research.request_timeout_seconds = 180
    os.environ["CLAUDE_CODE_MAX_RETRIES"] = "0"
    os.environ["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = "3500"
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    workspace = output / "workspace"
    workspace.mkdir(exist_ok=True, mode=0o700)
    store = Store(output / "metadata")  # Account metadata only; no run rows.
    router = TaskRouter(config)
    limits = {
        provider: SubscriptionLimits(store, provider, config.subscription)
        for provider in ("openai", "anthropic")
    }
    new_calls = 0

    async def call(label, provider, instruction, data, schema=None):
        nonlocal new_calls
        target = output / (label + ".json")
        if target.exists():
            saved = json.loads(target.read_text(encoding="utf-8"))
            if saved.get("status") != "parsed" or saved.get("run_id") != run["id"]:
                raise ValueError(f"Inspect and preserve the earlier result before retrying {label}")
            print(json.dumps({"check": label, "status": "reused_saved_live_result"}), flush=True)
            return saved["parsed"]
        request = config.model_copy(deep=True)
        selection = router.select(
            "MB" if provider == "openai" else "JB",
            "mb-message" if provider == "openai" else "guidance-audit-validation",
        )
        brain = request.rb if provider == "openai" else request.jb
        brain.model, brain.effort = selection.model, selection.effort
        if provider == "openai":
            quota = await CodexAccount(request, workspace).snapshot()
            limits[provider].codex(quota["limits"])
        else:
            quota = await ClaudeAccount(request, workspace).snapshot()
            limits[provider].claude_snapshot(
                quota["limits"], observed=quota.get("observed"), model=brain.model
            )
        if limits[provider].remaining():
            raise ValueError("Account cooldown: no validation prompt sent")
        instruction += (
            "\nRead-only evaluation of completed research. Do not start or resume work, "
            "claim fresh retrieval, or invent owner answers."
        )
        if schema:
            instruction += "\nReturn ONLY JSON matching: " + json.dumps(schema.model_json_schema())
        else:
            instruction += "\nReturn ONLY JSON."
        started = time.monotonic()
        result = await NativeClient(provider, request, workspace, limits[provider]).complete(
            prompt(instruction, data, config.research.max_context_chars), False
        )
        new_calls += 1
        record = {
            "check": label,
            "run_id": run["id"],
            "status": "received",
            "model": brain.model,
            "effort": brain.effort,
            "elapsed_seconds": time.monotonic() - started,
            "result": result,
        }
        target.write_text(json.dumps(record, indent=2), encoding="utf-8")
        target.chmod(0o600)
        text = result["text"].strip()
        if text.startswith("```"):
            text = "\n".join(text.splitlines()[1:-1])
        parsed = schema.model_validate_json(text).model_dump() if schema else json.loads(text)
        record.update(status="parsed", parsed=parsed)
        target.write_text(json.dumps(record, indent=2), encoding="utf-8")
        print(
            json.dumps(
                {"check": label, "status": "parsed", "model": brain.model, "usage": result["usage"]}
            ),
            flush=True,
        )
        return parsed

    def require_audit(audit):
        if not all(
            audit[key]
            for key in ("distinct_topics", "new_information", "easy_to_answer", "relevant_to_round")
        ):
            raise ValueError("Live question audit rejected the batch: " + json.dumps(audit))

    try:
        router.set_catalog("rb", await load_models("rb", config, workspace))
        if args.provider == "both":
            router.set_catalog("jb", await load_models("jb", config, workspace))
            await claude_account(config, workspace)
        contract = await call("intake", "openai", INTAKE, {"problem": run["problem"]}, Intake)
        data = {
            "problem": run["problem"],
            "research_contract": contract,
            "latest_review": rounds[-1]["review"],
            "question_limit": 3,
            "owner_feedback": [],
            "prior_questions": [],
        }
        first = await call("questions-first", "openai", QUESTION_PROMPT, data, Questions)
        validate_questions(first, [], 3)
        next_data = data | {
            "prior_questions": first["questions"],
            "validation_note": "No answers have been provided. Propose different topics without inventing answers.",
        }
        second = await call("questions-next", "openai", QUESTION_PROMPT, next_data, Questions)
        validate_questions(second, first["questions"], 3)
        if args.provider == "both":
            for label, source, batch in (("first", data, first), ("next", next_data, second)):
                audit = await call(
                    "question-audit-" + label,
                    "anthropic",
                    QUESTION_AUDIT,
                    source | {"proposed_questions": batch["questions"]},
                    QuestionAudit,
                )
                require_audit(audit)
            await call(
                "research-audit",
                "anthropic",
                "Evaluate whether the research answered the ORIGINAL assignment and whether iteration was worthwhile. "
                "Return JSON with verdict, useful_findings (list), missing_outputs (list), wasted_work (list), "
                "recommended_changes (list). A reviewer score is not independent quality evidence.",
                {
                    "problem": run["problem"],
                    "final_plan": rounds[-1]["plan"],
                    "final_review": rounds[-1]["review"],
                    "late_progress": [
                        {"round": row["number"], "review": row["review"]} for row in rounds[-6:]
                    ],
                    "calls": statistics,
                },
            )
            await call(
                "review-schema",
                "anthropic",
                JUDGE,
                {
                    "problem": run["problem"],
                    "research_contract": contract,
                    "plan": rounds[-1]["plan"],
                },
                Review,
            )
        summary = {
            "status": "passed" if args.provider == "both" else "codex_only_claude_not_checked",
            "new_live_calls": new_calls,
            "research_id": run["id"],
            "created_or_resumed_research": False,
            "topics": [row["topic"] for row in first["questions"] + second["questions"]],
        }
        (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(summary), flush=True)
    finally:
        store.close()


if __name__ == "__main__":
    asyncio.run(main())
