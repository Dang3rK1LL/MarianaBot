from marianabot.model_catalog import parse_models


def test_codex_catalog_keeps_model_specific_efforts_and_excludes_hidden_models():
    models = parse_models(
        "rb",
        [
            {
                "model": "gpt-6.1-sol",
                "displayName": "Sol",
                "defaultReasoningEffort": "low",
                "supportedReasoningEfforts": [
                    {"reasoningEffort": "low"},
                    {"reasoningEffort": "high"},
                    {"reasoningEffort": "ultra"},
                ],
            },
            {"model": "hidden-model", "hidden": True},
            {"model": "invalid model; shell"},
        ],
    )
    assert len(models) == 1
    assert models[0].efforts == ("low", "high")
    assert models[0].default_effort == "low"


def test_claude_catalog_pins_aliases_deduplicates_defaults_and_handles_fixed_effort():
    models = parse_models(
        "jb",
        [
            {
                "value": "default",
                "resolvedModel": "claude-opus-5-5",
                "supportsEffort": True,
                "supportedEffortLevels": ["low", "high"],
            },
            {
                "value": "opus",
                "resolvedModel": "claude-opus-5-5",
                "displayName": "Opus",
                "description": "Opus 5.5 · Best for everyday tasks",
                "supportsEffort": True,
                "supportedEffortLevels": ["low", "high"],
            },
            {"value": "haiku", "resolvedModel": "claude-haiku-4-5-20251001"},
            {"value": "unknown", "supportsEffort": True},
        ],
    )
    assert [model.model for model in models] == ["claude-opus-5-5", "claude-haiku-4-5-20251001"]
    assert models[0].label == "Opus 5.5" and models[0].efforts == ("low", "high")
    assert models[1].efforts == () and models[1].default_effort == "auto"


def test_missing_codex_effort_metadata_uses_default_without_guessing_levels():
    model = parse_models("rb", [{"model": "legacy-model"}])[0]
    assert model.efforts == () and model.default_effort == "auto"
