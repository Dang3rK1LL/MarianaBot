"""Offline subprocess fixture for official client protocol contracts."""

import json
import os
import sys

args = sys.argv[1:]


def emit(value):
    print(json.dumps(value), flush=True)


if "app-server" in args:
    for line in sys.stdin:
        event = json.loads(line)
        method = event.get("method")
        if method == "initialize":
            emit({"id": event["id"], "result": {"userAgent": "fixture"}})
        elif method == "account/read":
            emit(
                {"id": event["id"], "result": {"account": {"type": "chatgpt", "planType": "plus"}}}
            )
        elif method == "account/rateLimits/read":
            emit(
                {
                    "id": event["id"],
                    "result": {
                        "rateLimits": {"primary": {"usedPercent": 5, "resetsAt": 9999999999}}
                    },
                }
            )
        elif method == "model/list":
            emit(
                {
                    "id": event["id"],
                    "result": {"data": [{"model": "gpt-6-astra"}], "nextCursor": None},
                }
            )
elif "auth" in args:
    emit({"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "pro"})
else:
    assert not os.getenv("OPENAI_API_KEY")
    assert not os.getenv("ANTHROPIC_API_KEY")
    prompt = sys.stdin.read()
    if "--json" in args:
        emit(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "Fixture: " + prompt},
            }
        )
        emit({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 20}})
    else:
        emit(
            {"type": "system", "subtype": "init", "model": "claude-opus-5", "apiKeySource": "none"}
        )
        emit(
            {
                "type": "rate_limit_event",
                "rate_limit_info": {"status": "allowed", "utilization": 0.2},
            }
        )
        emit(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Fixture: " + prompt,
                "usage": {"input_tokens": 10, "output_tokens": 20},
                "total_cost_usd": 0.001,
            }
        )
