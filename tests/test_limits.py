import pytest

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


def test_claude_account_percentages_resets_and_scoped_limits(store, config):
    limits = SubscriptionLimits(store, "anthropic", config.subscription)
    limits.claude_snapshot(
        {
            "five_hour": {"utilization": 1.2, "resets_at": "1970-01-01T00:08:20Z"},
            "seven_day": {"utilization": 96, "resets_at": "1970-01-01T01:00:00+00:00"},
            "seven_day_opus": None,
            "model_scoped": [{"display_name": "Opus", "utilization": 22, "resets_at": 700}],
            "extra_usage": {"utilization": 100, "is_enabled": False},
        },
        now=100,
        observed=90,
        model="claude-opus-5-5",
    )
    windows = limits.data["windows"]
    assert [w["percent"] for w in windows] == [1.2, 96, 22]
    assert [w["reset"] for w in windows] == [500, 3600, 700]
    assert all(w["observed"] == 90 for w in windows)
    assert limits.remaining(now=100) == 3500
    assert "extra_usage" not in [w["name"] for w in windows]


@pytest.mark.parametrize("value", [None, "invalid", float("nan"), -5, 200])
def test_claude_unreadable_snapshot_keeps_previous_measurement(store, config, value):
    limits = SubscriptionLimits(store, "anthropic", config.subscription)
    limits.claude_snapshot({"five_hour": {"utilization": 25}}, now=100)
    saved = store.get_limits("anthropic")
    with pytest.raises(ValueError, match="no readable"):
        limits.claude_snapshot({"five_hour": {"utilization": value}}, now=200)
    assert store.get_limits("anthropic") == saved


def test_claude_model_scoped_capacity_only_applies_to_selected_model(store, config):
    limits = SubscriptionLimits(store, "anthropic", config.subscription)
    payload = {
        "five_hour": {"utilization": 0},
        "seven_day_sonnet": {"utilization": 98, "resets_at": 500},
    }
    limits.claude_snapshot(payload, now=100, model="claude-opus-5-5")
    assert limits.remaining(now=100) == 0
    limits.claude_snapshot(payload, now=100, model="claude-sonnet-4-6")
    assert limits.remaining(now=100) == 400


def test_claude_unknown_stream_event_preserves_other_process_snapshot_and_its_age(store, config):
    worker = SubscriptionLimits(store, "anthropic", config.subscription)
    other = SubscriptionLimits(store, "anthropic", config.subscription)
    other.claude_snapshot({"five_hour": {"utilization": 32}}, now=100)
    worker.claude({"status": "allowed", "rateLimitType": "five_hour"}, now=200)
    window = store.get_limits("anthropic")["windows"][0]
    assert window["percent"] == 32
    assert window["observed"] == 100
    assert window["status"] == "allowed"


def test_claude_cached_account_snapshot_does_not_erase_a_newer_stream_measurement(store, config):
    limits = SubscriptionLimits(store, "anthropic", config.subscription)
    limits.claude({"rateLimitType": "five_hour", "utilization": 0.34}, now=200)
    limits.claude_snapshot({"five_hour": {"utilization": 25}}, now=210, observed=100)
    window = store.get_limits("anthropic")["windows"][0]
    assert window["percent"] == 34
    assert window["observed"] == 200
