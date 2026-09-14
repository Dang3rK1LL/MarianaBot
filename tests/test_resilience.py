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


async def test_mb_rb_share_one_concurrency_gate(store, config):
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
        engine.call("MB", "a", "MASTER_INTAKE", {}), engine.call("RB", "b", "Research", {})
    )
    assert client.peak == 1


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


def test_reopen_store_preserves_checkpoints(store, config):
    run_id = store.create_run("Durable checkpoint", config, demo=True)
    call = store.begin_call(run_id, "stable", "RB", "openai", config.rb.model)
    store.finish(call, "done", {"text": "saved"})
    second = Store(store.directory)
    try:
        assert second.cached(run_id, "stable")["text"] == "saved"
    finally:
        second.close()
