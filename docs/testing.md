# Validation

The shipped app has no demo client, fixture research or fabricated fallback
responses. Earlier simulated records remain distinguishable and read-only.

## Static checks

```text
python -m pip install -e ".[dev,discord]"
python -m ruff check src scripts tests
python -m ruff format --check src scripts tests
python -m compileall -q src scripts tests
python -m unittest discover -s tests -v
```

CI runs these checks on Windows and Ubuntu with Python 3.11, 3.13 and 3.14,
plus imports of the CLI, guided schemas and Discord adapter. Offline tests exercise
question checkpoints, owner controls, context retention and stopping rules in
temporary stores. They make no provider calls and do not establish research quality.
CI never runs model prompts or produces research outputs.

## Live checks

[`scripts/live_review.py`](../scripts/live_review.py) reads an existing completed
research from a private SQLite backup. It makes real calls to validate intake,
two batches of distinct guided questions, their independent audits and the review
schema. It also evaluates the original assignment against the final output.
It never creates, starts or resumes a research run and never invents owner answers.

```text
python scripts/live_review.py --snapshot PRIVATE_BACKUP.sqlite3 --run-id COMPLETED_ID --output .mariana/live-review
```

Run it on the host with your subscription logins and disabled overage settings.
It consumes allowance. `--provider openai` performs only the Codex checks and
explicitly reports that Claude was not checked. Successful parsed responses are
saved and reused on a rerun; a rejected audit still fails validation. Inspect and
preserve a failed result before retrying with a new output directory.

[`scripts/live_smoke.py`](../scripts/live_smoke.py) offers a smaller explicit
connectivity check with real provider requests. Neither script runs on startup.
`mariana doctor` and model/allowance refreshes read account metadata without
research inference. Provider responses and research remain private under `.mariana/`.

Assess quality by comparing requested deliverables with actual outputs, source
support, decision changes and calls spent. Reviewer scores alone do not measure
quality. Guided answer submission and automatic continuation require observation
in a real user-started run; isolated provider checks do not establish that full flow.
