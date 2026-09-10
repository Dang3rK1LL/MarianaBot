"""Official CLI integrations. Prompts travel over stdin; no shell executes model text."""

import asyncio
import json
import os
import shutil
import signal
import subprocess
from pathlib import Path

from marianabot.config import Config
from marianabot.limits import SubscriptionLimits

MAX_CAPTURE = 12 * 1024 * 1024


class ClientError(Exception):
    def __init__(self, message: str, retryable: bool = False, limited: bool = False):
        super().__init__(message)
        self.retryable, self.limited = retryable, limited


def subscription_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in list(env):
        upper = key.upper()
        if upper.startswith(("OPENAI_", "ANTHROPIC_", "CLAUDE_CODE_USE_")) or upper in {
            "CODEX_API_KEY",
            "CLAUDE_CODE_OAUTH_TOKEN",
            "CLAUDECODE",
        }:
            env.pop(key)
    env.update(
        CLAUDE_CODE_DISABLE_FAST_MODE="1",
        CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
        CLAUDE_CODE_DISABLE_BACKGROUND_TASKS="1",
        CLAUDE_CODE_SAFE_MODE="1",
    )
    return env


def executable(command: str) -> list[str]:
    found = shutil.which(command)
    if not found:
        raise ClientError(f"Client not found: {command}. Install the official CLI and run doctor.")
    path = Path(found)
    if path.suffix.lower() in (".cmd", ".bat", ".ps1"):
        # Resolve the official npm package without cmd.exe or shell quoting.
        root = path.parent / "node_modules" / "@openai" / "codex"
        if path.stem.lower() == "codex":
            candidates = list(root.glob("node_modules/@openai/codex-*/vendor/*/codex/codex.exe"))
            candidates += list(root.glob("vendor/*/codex/codex.exe"))
            if len(candidates) == 1:
                return [str(candidates[0])]
            script = root / "bin" / "codex.js"
            node = shutil.which("node")
            if script.is_file() and node:
                return [node, str(script)]
        raise ClientError("Use a native executable path; shell-script launchers are not supported.")
    return [str(path)]


async def launch(args: list[str], cwd: Path):
    kwargs = (
        {"creationflags": subprocess.CREATE_NO_WINDOW}
        if os.name == "nt"
        else {"start_new_session": True}
    )
    return await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=subscription_env(),
        limit=MAX_CAPTURE,
        **kwargs,
    )


async def terminate(process):
    if process.returncode is not None:
        return
    if os.name == "nt":
        killer = await asyncio.create_subprocess_exec(
            "taskkill",
            "/PID",
            str(process.pid),
            "/T",
            "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        await killer.wait()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        await asyncio.wait_for(process.wait(), 5)
    except TimeoutError:
        process.kill()
        await process.wait()


async def capture(args: list[str], cwd: Path, timeout: float = 30) -> tuple[int, bytes, bytes]:
    process = await launch(args, cwd)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
        if len(stdout) + len(stderr) > MAX_CAPTURE:
            raise ClientError("Client diagnostics exceeded the capture limit")
        return process.returncode, stdout, stderr
    finally:
        await terminate(process)


async def drain(stream):
    # Read continuously without retaining private client diagnostic logs.
    while await stream.read(8192):
        pass


class CodexAccount:
    def __init__(self, config: Config, cwd: Path):
        self.config, self.cwd = config, cwd

    async def snapshot(self, include_models: bool = False) -> dict:
        args = executable(self.config.subscription.codex_command) + [
            "-c",
            'forced_login_method="chatgpt"',
            "app-server",
            "--listen",
            "stdio://",
        ]
        process = await launch(args, self.cwd)
        stderr_task = asyncio.create_task(drain(process.stderr))
        request_id = 0

        async def send(payload):
            process.stdin.write((json.dumps(payload) + "\n").encode())
            await process.stdin.drain()

        async def rpc(method, params=None):
            nonlocal request_id
            request_id += 1
            await send({"id": request_id, "method": method, "params": params or {}})
            while True:
                line = await process.stdout.readline()
                if not line:
                    raise ClientError("Codex account service closed before responding")
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("id") == request_id:
                    if "error" in event:
                        raise ClientError(
                            f"Codex {method} failed; check login, network and CLI version"
                        )
                    return event.get("result", {})

        try:
            async with asyncio.timeout(40):
                await rpc("initialize", {"clientInfo": {"name": "marianabot", "version": "0.1.0"}})
                await send({"method": "initialized", "params": {}})
                account = await rpc("account/read", {"refreshToken": True})
                account_data = account.get("account") or {}
                if account_data.get("type") != "chatgpt":
                    raise ClientError("Codex must be signed in with ChatGPT. Run codex login.")
                limits = await rpc("account/rateLimits/read")
                result = {"auth": "chatgpt", "plan": account_data.get("planType"), "limits": limits}
                if include_models:
                    models, cursor = [], None
                    for _ in range(20):
                        page = await rpc("model/list", {"cursor": cursor, "limit": 100})
                        models.extend(
                            item.get("model") or item.get("id") for item in page.get("data", [])
                        )
                        cursor = page.get("nextCursor")
                        if not cursor:
                            break
                    result["models"] = models
                return result
        except TimeoutError:
            raise ClientError(
                "Codex account check timed out; no model request was sent", retryable=True
            ) from None
        finally:
            await terminate(process)
            await stderr_task


async def claude_account(config: Config, cwd: Path) -> dict:
    code, output, _ = await capture(
        executable(config.subscription.claude_command) + ["auth", "status", "--json"], cwd
    )
    try:
        payload = json.loads(output)
    except ValueError:
        raise ClientError(
            "Claude auth status did not return JSON; update the official CLI"
        ) from None
    if code or not payload.get("loggedIn") or payload.get("authMethod") != "claude.ai":
        raise ClientError(
            "Claude must be signed in with your Claude subscription. Run claude auth login."
        )
    return {"auth": "claude.ai", "plan": payload.get("subscriptionType")}


def error_from(text: str) -> ClientError:
    lower = text.lower()
    if any(
        word in lower
        for word in (
            "rate_limit",
            "rate limit",
            "usage limit",
            "usage_limit",
            "limit reached",
            "hit your limit",
        )
    ):
        return ClientError("Subscription usage limit reached", limited=True)
    if any(
        word in lower
        for word in (
            "overloaded",
            "server_error",
            "server error",
            "timeout",
            "timed out",
            "connection",
        )
    ):
        return ClientError("Provider temporarily unavailable", retryable=True)
    if any(
        word in lower
        for word in ("model_not_found", "model not found", "not supported", "not available")
    ):
        return ClientError(
            "Requested model is unavailable for this account; no fallback model was selected"
        )
    if any(word in lower for word in ("auth", "login", "unauthorized")):
        return ClientError("Client authentication failed; sign in again with your subscription")
    return ClientError(
        "Client request failed; inspect account access and client version with mariana doctor"
    )


class NativeClient:
    def __init__(self, provider: str, config: Config, cwd: Path, limits: SubscriptionLimits):
        self.provider, self.config, self.cwd, self.limits = provider, config, cwd, limits
        cwd.mkdir(parents=True, exist_ok=True)

    def args(self, search: bool) -> list[str]:
        if self.provider == "openai":
            brain = self.config.rb
            args = executable(self.config.subscription.codex_command) + [
                "exec",
                "--ignore-user-config",
                "-c",
                'forced_login_method="chatgpt"',
                "-c",
                'model_provider="openai"',
                "-c",
                'approval_policy="never"',
                "-c",
                f'model_reasoning_effort="{brain.effort}"',
                "-c",
                f'web_search="{"live" if search else "disabled"}"',
                "-c",
                "project_doc_max_bytes=0",
                "--json",
                "--ephemeral",
                "--ignore-rules",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--model",
                brain.model,
            ]
            for feature in (
                "shell_tool",
                "unified_exec",
                "apps",
                "plugins",
                "hooks",
                "multi_agent",
                "browser_use",
                "computer_use",
                "image_generation",
                "memories",
                "goals",
            ):
                args += ["--disable", feature]
            return args + ["-"]
        brain = self.config.jb
        return executable(self.config.subscription.claude_command) + [
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--model",
            brain.model,
            "--effort",
            brain.effort,
            "--safe-mode",
            "--restricted",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--setting-sources",
            "",
            "--tools",
            "WebSearch" if search else "",
            "--allowedTools",
            "WebSearch" if search else "",
            "--permission-mode",
            "dontAsk",
            "--no-session-persistence",
            "--no-chrome",
            "--disable-slash-commands",
        ]

    async def complete(self, prompt: str, search: bool = False) -> dict:
        process = await launch(self.args(search), self.cwd)
        stderr_task = asyncio.create_task(drain(process.stderr))
        result = None
        messages, usage, sources = [], {}, []
        failure = None
        total = 0
        try:
            process.stdin.write(prompt.encode("utf-8"))
            await process.stdin.drain()
            process.stdin.close()
            async with asyncio.timeout(self.config.research.request_timeout_seconds):
                while line := await process.stdout.readline():
                    total += len(line)
                    if total > MAX_CAPTURE:
                        raise ClientError("Client output exceeded the capture limit")
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    kind = event.get("type")
                    if self.provider == "openai":
                        if kind == "item.completed":
                            item = event.get("item", {})
                            if item.get("type") == "agent_message":
                                messages.append(item.get("text", ""))
                            if item.get("type") == "web_search":
                                sources.append(item)
                        elif kind == "turn.completed":
                            usage = event.get("usage", {})
                            result = {
                                "text": messages[-1] if messages else "",
                                "usage": usage,
                                "sources": sources,
                            }
                        elif kind in ("turn.failed", "error"):
                            failure = error_from(json.dumps(event))
                    else:
                        if kind == "rate_limit_event":
                            self.limits.claude(event.get("rate_limit_info", {}))
                        elif kind == "system" and event.get("subtype") == "init":
                            if event.get("model") != self.config.jb.model:
                                raise ClientError(
                                    "Claude selected a different model; stopping to preserve your model choice"
                                )
                            # apiKeySource may say "none"; any actual key source is rejected.
                            if event.get("apiKeySource") not in (None, "none", ""):
                                raise ClientError(
                                    "Claude reported API-key authentication; subscription mode refuses it"
                                )
                        elif kind == "result":
                            if event.get("is_error") or event.get("subtype") != "success":
                                failure = error_from(json.dumps(event))
                            else:
                                text = event.get("result", "")
                                if event.get("structured_output") is not None:
                                    text = json.dumps(event["structured_output"])
                                result = {
                                    "text": text,
                                    "usage": event.get("usage", {}),
                                    "sources": sources,
                                    "api_equivalent_usd": event.get("total_cost_usd"),
                                }
                        elif kind == "assistant":
                            for block in event.get("message", {}).get("content", []):
                                if (
                                    block.get("type") == "tool_use"
                                    and block.get("name") == "WebSearch"
                                ):
                                    sources.append(
                                        {"tool": "WebSearch", "input": block.get("input")}
                                    )
                code = await process.wait()
                if code or not result:
                    raise failure or ClientError(
                        "Client ended without a completed response", retryable=True
                    )
                if not result["text"].strip():
                    raise ClientError("Client returned an empty final response", retryable=True)
                result["model"] = (
                    self.config.rb.model if self.provider == "openai" else self.config.jb.model
                )
                return result
        except TimeoutError:
            raise ClientError(
                "Client request timed out; provider usage may already have occurred", retryable=True
            ) from None
        finally:
            await terminate(process)
            await stderr_task


class DemoClient:
    """Deterministic, visibly labeled fixtures; never invokes a model or network."""

    async def complete(self, prompt: str, search: bool = False) -> dict:
        await asyncio.sleep(0.06)
        if "JUDGE_JSON" in prompt:
            text = json.dumps(
                {
                    "score": 72,
                    "verdict": "revise",
                    "strengths": ["A measurable pilot precedes larger investment"],
                    "blocking_issues": ["Customer demand and acquisition costs remain unvalidated"],
                    "next_prompt": "Specify a small customer pilot and explicit go/no-go thresholds.",
                    "human_tests": [
                        "Interview 10 potential buyers and seek 3 paid pilot commitments"
                    ],
                    "dissent": ["A stronger narrative is not evidence of demand"],
                }
            )
        elif "MASTER_INTAKE" in prompt or "MASTER_STEER" in prompt:
            text = (
                "# DEMO research brief\n\nInvestigate the supplied problem with a small, reversible pilot.\n"
                "Track demand, unit economics and operational constraints.\n"
                "Assumptions: budget and target customers still need confirmation.\n"
                "Questions for the owner: who is the first paying customer, and what is the loss limit?\n"
                "Success: a costed action plan with falsifiable milestones."
            )
        elif "MASTER_ANSWER" in prompt:
            text = "DEMO MB: The latest draft still needs evidence from customer interviews and a paid pilot."
        else:
            text = (
                "# DEMO business and action plan\n\nThis is an offline fixture, not model-generated research.\n\n"
                "1. Interview 10 target customers during week one; owner: founder.\n"
                "2. Offer 3 paid pilots during week two; set a loss limit before launch.\n"
                "3. Record acquisition cost, delivery time and contribution margin.\n"
                "4. Proceed only if buyers commit and the pilot can achieve positive contribution margin.\n\n"
                "Unknowns: willingness to pay, acquisition costs and local requirements.\n"
                "Dissent: stop if enthusiasm fails to turn into commitments.\n"
                "Sources: none; illustrative demo only."
            )
        return {
            "text": text,
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "sources": [],
            "model": "offline-demo",
        }
