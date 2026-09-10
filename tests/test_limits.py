from marianabot.limits import SubscriptionLimits


def test_codex_all_windows_and_persistent_reset(store, config):
    limits = SubscriptionLimits(store, "openai", config.subscription)
    limits.codex(
        {
            "rateLimitsByLimitId": {
                "codex": {
                    "limitId": "codex",
                    "primary": {"usedPercent": 20, "resetsAt": 150},
                    "secondary": {"usedPercent": 98, "resetsAt": 500},
                },
                "astra": {"limitId": "astra", "primary": {"usedPercent": 100, "resetsAt": 700}},
            }
        },
        now=100,
    )
    assert limits.remaining(now=100) == 600
    restored = SubscriptionLimits(store, "openai", config.subscription)
    assert restored.remaining(now=100) == 600
    assert restored.remaining(now=701) == 0
    assert len(restored.data["windows"]) == 3


def test_missing_reset_uses_conservative_wait(store, config):
    limits = SubscriptionLimits(store, "openai", config.subscription)
    limits.codex({"rateLimits": {"primary": {"usedPercent": 100}}}, now=100)
    assert limits.remaining(now=100) == config.subscription.unknown_reset_wait_seconds


def test_claude_fraction_and_rejection(store, config):
    limits = SubscriptionLimits(store, "anthropic", config.subscription)
    limits.claude({"status": "allowed_warning", "utilization": 0.96, "resetsAt": 500}, now=100)
    assert limits.data["windows"][0]["percent"] == 96
    assert limits.remaining(now=100) == 400
    limits.claude({"status": "rejected"}, now=100)
    assert limits.remaining(now=100) == config.subscription.unknown_reset_wait_seconds


def test_claude_unknown_usage_is_not_displayed_as_zero(store, config):
    limits = SubscriptionLimits(store, "anthropic", config.subscription)
    limits.claude({"status": "allowed"}, now=100)
    assert limits.data["windows"][0]["percent"] is None
