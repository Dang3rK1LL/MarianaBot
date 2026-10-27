"""Offline state/control regressions; no provider calls or generated research."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from marianabot.clients import ClientError
from marianabot.config import Config
from marianabot.engine import Engine, Halt, stop_reason
from marianabot.guidance import Questions
from marianabot.memory import size
from marianabot.store import Store

AUDIT = dict(
    distinct_topics=True,
    new_information=True,
    easy_to_answer=True,
    relevant_to_round=True,
    corrections=[],
)
QUESTION = dict(
    topic="weekly_capacity",
    question="How many hours are available each week?",
    options=["Five", "Ten"],
    impact="Sets the maximum weekly delivery capacity.",
)


class EngineControls(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(Path(self.temporary.name))
        self.addCleanup(self.store.close)
        self.config = Config()
        self.config.research.mode = "guided"
        self.config.research.max_retries = 0
        self.run_id = self.store.create_run("Control regression", self.config)
        self.engine = Engine(self.store, self.run_id)

    def saved_call(self, task, parsed):
        identifier = self.store.begin_call(self.run_id, task, "MB", "openai", "test")
        result = {"text": json.dumps(parsed), "parsed": parsed}
        self.store.finish(identifier, "done", result)
        return result

    async def test_audited_empty_questions_continue_and_reuse_checkpoints(self):
        Questions.model_validate({"questions": []})
        generated = []

        async def call(brain, task, *args, **kwargs):
            cached = self.store.cached(self.run_id, task)
            if cached:
                return cached
            generated.append(task)
            return self.saved_call(task, AUDIT if brain == "JB" else {"questions": []})

        self.engine.call = call
        self.assertEqual(await self.engine.round_questions(1, []), [])
        self.assertEqual(await self.engine.round_questions(1, []), [])
        self.assertEqual(len(generated), 2)
        self.assertEqual(self.store.guidance(self.run_id), [])
        self.assertNotEqual(self.store.run(self.run_id)["status"], "awaiting_answers")
        notices = [m for m in self.store.messages(self.run_id) if "No questions" in m["title"]]
        self.assertEqual(len(notices), 1)

    async def test_rejected_audits_do_not_make_resume_replay_forever(self):
        tasks, feedback = [], []

        async def call(brain, task, instruction, data, **kwargs):
            tasks.append(task)
            if brain == "MB":
                feedback.append(data["audit_feedback"])
            parsed = (
                AUDIT | {"new_information": False, "corrections": ["Ask nothing"]}
                if brain == "JB"
                else {"questions": [QUESTION]}
            )
            return self.saved_call(task, parsed)

        self.engine.call = call
        for _ in range(2):
            with self.assertRaises(Halt):
                await self.engine.round_questions(1, [])
        self.assertTrue(tasks[0].endswith("attempt-0"))
        self.assertTrue(tasks[2].endswith("attempt-1"))
        self.assertEqual(feedback, [[], ["Ask nothing"]])
        self.assertEqual(self.store.guidance(self.run_id), [])

    async def test_empty_batch_still_requires_successful_audit(self):
        self.engine.call = AsyncMock(
            side_effect=[
                {"parsed": {"questions": []}},
                {"parsed": AUDIT | {"new_information": False}},
            ]
        )
        with self.assertRaises(Halt):
            await self.engine.round_questions(1, [])
        self.assertFalse(
            any("No questions" in m["title"] for m in self.store.messages(self.run_id))
        )

    async def test_owner_change_invalidates_empty_question_checkpoint(self):
        tasks = []

        async def call(brain, task, *args, **kwargs):
            tasks.append(task)
            return self.saved_call(task, AUDIT if brain == "JB" else {"questions": []})

        self.engine.call = call
        await self.engine.round_questions(1, [])
        self.store.pin(self.run_id, "The weekly capacity has changed.")
        await self.engine.round_questions(1, [])
        self.assertNotEqual(tasks[0], tasks[2])

    async def test_existing_unanswered_questions_cannot_be_skipped(self):
        self.store.save_guidance(self.run_id, 1, [QUESTION])
        self.engine.call = AsyncMock(side_effect=AssertionError("Must use saved questions"))
        with self.assertRaises(Halt) as caught:
            await self.engine.round_questions(1, [])
        self.assertEqual(caught.exception.status, "awaiting_answers")
        self.assertIsNone(self.store.guidance(self.run_id)[0]["answer"])

    async def test_owner_pause_survives_last_answer(self):
        self.store.save_guidance(self.run_id, 1, [QUESTION])
        self.store.await_guidance(self.run_id, 1)
        self.store.update_run(self.run_id, control="pause", status="paused")
        self.assertTrue(self.store.answer_guidance(self.run_id, "R1Q1", "Ten"))
        self.assertEqual(self.store.run(self.run_id)["control"], "pause")
        with self.assertRaises(Halt):
            self.engine.check()

    async def test_mb_reply_does_not_require_research_ceiling_or_claude(self):
        self.engine.messages_only = True
        self.engine.config.subscription.overage_disabled = True
        self.engine.refresh_account = AsyncMock(
            return_value={
                "models": [self.config.mb.model],
                "model_details": [{"model": self.config.mb.model}],
            }
        )
        with patch("marianabot.engine.claude_account", new_callable=AsyncMock) as claude:
            await self.engine.preflight()
            claude.assert_not_awaited()
        self.assertIn(self.config.mb.model, self.engine.router.catalogs["rb"])

    async def test_compaction_keeps_all_critics_and_exact_review_subject(self):
        plan = "Exact plan quotation. " * 60
        parts = {
            "problem": "Owner problem",
            "plan": plan,
            "current_round_answers": [{"answer": "Ten hours"}],
            "independent_critiques": [
                f"Critic {number} evidence remains distinct. " * 400 for number in range(3)
            ],
            "question_limit": 3,
        }
        result = await self.engine.memory.prepare(parts, 8000)
        self.assertLessEqual(size(result), 8000)
        self.assertEqual(result["plan"], plan)
        self.assertEqual(result["current_round_answers"], parts["current_round_answers"])
        self.assertEqual(result["question_limit"], 3)
        self.assertIsInstance(result["independent_critiques"], list)
        for number, text in enumerate(result["independent_critiques"]):
            self.assertIn(f"Critic {number}", text)
            self.assertTrue(text.startswith("Partial archive extract"))

    async def test_nested_compaction_preserves_containers(self):
        value = {"first": ["Evidence A. " * 600], "second": {"text": "Evidence B. " * 600}}
        packed = self.engine.memory.pack_value(value, 3000, "evidence")
        self.assertLessEqual(size(packed), 3000)
        self.assertIsInstance(packed["first"], list)
        self.assertIsInstance(packed["second"], dict)
        self.assertIn("Evidence B", packed["second"]["text"])

    async def test_protected_context_overflow_pauses_without_trimming(self):
        parts = {"problem": "Owner text " * 1000, "plan": "Exact review subject"}
        with self.assertRaises(ClientError):
            await self.engine.memory.prepare(parts, 8000)
        self.assertEqual(parts["plan"], "Exact review subject")


class CompletionControls(unittest.TestCase):
    def setUp(self):
        self.config = Config()
        self.contract = {"deliverables": [{"id": "draft", "kind": "creative"}]}
        self.review = dict(
            verdict="approve",
            score=90,
            foundation_ready=True,
            decision="hold",
            decision_ready=True,
            material_change=False,
            blocking_issues=[],
            online_checks=[],
            deliverables={"draft": "complete"},
        )

    def reason(self, **changes):
        review = self.review | changes
        rounds = [{"revision": 0, "review": review} for _ in range(2)]
        return stop_reason(rounds, self.config, self.contract)

    def test_completed_conditional_draft_allows_stable_hold(self):
        self.assertIn("Stable decision", self.reason())

    def test_partial_draft_blocks_every_early_completion_path(self):
        self.assertIsNone(self.reason(deliverables={"draft": "partial"}))

    def test_missing_or_blocked_creative_output_cannot_finish(self):
        for statuses in ({}, {"draft": "blocked"}, {"draft": "unverifiable"}):
            with self.subTest(statuses=statuses):
                self.assertIsNone(self.reason(deliverables=statuses))

    def test_plateau_cannot_override_owner_constraints(self):
        self.assertIsNone(self.reason(scope_aligned=False, verdict="revise"))
        self.assertIsNone(self.reason(guidance_respected=False, verdict="revise"))

    def test_plateau_requires_delivery_ready_conclusion(self):
        self.assertIsNone(self.reason(verdict="revise", decision_ready=False))

    def test_unverifiable_external_fact_is_allowed_as_a_limitation(self):
        self.contract["deliverables"][0]["kind"] = "verification"
        self.assertIsNotNone(self.reason(deliverables={"draft": "unverifiable"}))


if __name__ == "__main__":
    unittest.main()
