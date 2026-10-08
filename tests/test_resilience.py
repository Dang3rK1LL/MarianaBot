import asyncio
import time

import pytest

from marianabot.clients import ClientError, DemoClient, diagnostic, error_from
from marianabot.engine import Engine, Halt
from marianabot.store import Store


async def test_limit_wait_is_persistent_and_does_not_spend_transient_retries(store, config):
    run_id = store.create_run("Wait until allowance resets", config, demo=True)

    class LimitedOnce(DemoClient):
        count = 0

        async def complete(self, prompt, search=False):
            self.count += 1
            if self.count == 1:
                raise ClientError("Usage limit", limited=True)
            return await super().complete(prompt, search)

    client = LimitedOnce()
    engine = Engine(store, run_id, clients={"openai": client, "anthropic": DemoClient()})
    waits = []

    async def simulated_wait(seconds, reason):
        waits.append(seconds)
        assert store.get_limits("openai")["until"] > time.time()
        engine.limits["openai"].data["until"] = time.time() - 1
        engine.limits["openai"].save()

    engine.wait = simulated_wait
    result = await engine.call("MB", "test", "MASTER_INTAKE", {"problem": "test"})
    assert result["text"]
    assert client.count == 2
    assert len(waits) == 1
    assert [c["state"] for c in store.calls(run_id)] == ["limited", "done"]


async def test_mb_has_one_separate_slot_while_rb_calls_remain_serial(store, config):
    run_id = store.create_run("Shared capacity", config, demo=True)

    class CountingClient(DemoClient):
        active = 0
        peak = 0

        async def complete(self, prompt, search=False):
            self.active += 1
            self.peak = max(self.peak, self.active)
            try:
                return await super().complete(prompt, search)
            finally:
                self.active -= 1

    client = CountingClient()
    engine = Engine(store, run_id, clients={"openai": client, "anthropic": DemoClient()})
    await asyncio.gather(
        engine.call("MB", "a", "MASTER_INTAKE", {}),
        engine.call("RB", "b", "Research", {}),
        engine.call("RB", "c", "Research", {}),
    )
    assert client.peak == 2
    assert {row["provider"] for row in store.calls(run_id)} == {"openai"}


async def test_duplicate_mailbox_read_does_not_duplicate_inference(store, config):
    run_id = store.create_run("Concurrent mailbox", config, demo=True)
    store.enqueue(run_id, "ask", "What is the status?")
    engine = Engine(store, run_id)
    await asyncio.gather(engine.handle_commands(False), engine.handle_commands(False))
    assert len(store.calls(run_id)) == 1


async def test_time_limit_prevents_even_intake(store, config):
    run_id = store.create_run("Already expired", config, demo=True)
    engine = Engine(store, run_id)
    engine.deadline = time.time() - 1
    await engine.run()
    assert store.run(run_id)["status"] == "complete"
    assert not store.calls(run_id)


async def test_cooldown_wait_can_be_paused_immediately(store, config):
    run_id = store.create_run("Pause during quota wait", config, demo=True)
    store.update_run(run_id, control="pause")
    with pytest.raises(Halt):
        await Engine(store, run_id).wait(3600, "cooldown")


def test_diagnostics_redact_secrets_and_escape_sequences():
    text = "\x1b[31mError sk-abcdef123 Bearer abcdef eyJabcdefghijklmnopqrstuvwxyz.123\x1b[0m"
    clean = diagnostic(text)
    assert "\x1b" not in clean
    assert "abcdef" not in clean
    assert clean.count("[redacted]") == 3


def test_billing_failure_is_not_retried_as_subscription_reset():
    error = error_from("billing_error: usage limit reached; spend cap")
    assert not error.retryable and not error.limited


@pytest.mark.parametrize(
    "error",
    [
        'API Error: 500 {"error":{"type":"api_error","message":"Unexpected failure"}}',
        '{"type":"result","subtype":"error_during_execution","errors":["Unexpected failure"]}',
        '{"type":"error","error":{"type":"overloaded_error","message":"Busy"}}',
        "API Error: No response from API",
    ],
)
def test_execution_and_server_errors_are_retryable(error):
    classified = error_from(error)
    assert classified.retryable and not classified.limited
    assert classified.diagnostics


@pytest.mark.parametrize(
    "reason",
    [
        "billing_error",
        "authentication_error",
        "model_not_found",
        "invalid_request_error",
        "prompt is too long",
        "error_max_turns",
        "error_max_budget_usd",
        "error_max_structured_output_retries",
    ],
)
def test_permanent_error_is_not_hidden_by_generic_execution_subtype(reason):
    classified = error_from(
        '{"type":"result","subtype":"error_during_execution","errors":["' + reason + '"]}'
    )
    assert not classified.retryable and not classified.limited


def test_quota_error_still_waits_and_success_metadata_cannot_change_error_classification():
    error = error_from(
        '{"type":"result","subtype":"error_during_execution","errors":["rate_limit_error"]}'
    )
    assert error.limited and not error.retryable
    generic = error_from(
        '{"type":"error","error":{"message":"Unrecognized failure"},"usage":{"authentication":true,"output_tokens":500}}'
    )
    assert str(generic).startswith("Client request failed")
    assert "usage" not in generic.diagnostics


def test_reopen_store_preserves_checkpoints(store, config):
    run_id = store.create_run("Durable checkpoint", config, demo=True)
    call = store.begin_call(run_id, "stable", "RB", "openai", config.rb.model)
    store.finish(call, "done", {"text": "saved"})
    second = Store(store.directory)
    try:
        assert second.cached(run_id, "stable")["text"] == "saved"
    finally:
        second.close()
