"""Model router: each job runs on an ordered fallback chain of model endpoints named in steer.toml.

Per endpoint: one call, one retry on failure, then the next endpoint. A failure is an error, a
timeout, output that is not JSON, or JSON that does not match the job's schema. Every attempt is
logged (job, endpoint, connector, model, latency, outcome, tokens only when the connector reports
them); credentials never appear in the log. Nothing unvalidated is returned.
"""

import json
import os
import re
import subprocess
import tempfile
import time
import tomllib
import uuid
from pathlib import Path

import httpx
import jsonschema

DEFAULT_SETTINGS = Path(__file__).resolve().parents[2] / "steer.toml"
USER_AGENT = "steer-pipeline/0.1 (hackathon prototype)"
CLAUDE_FLAGS = [  # minimal, tool-less, non-interactive; checked against `claude --help` (Claude Code 2.1.302)
    "--output-format", "json",
    "--tools", "",
    "--strict-mcp-config",
    "--setting-sources", "",
    "--no-session-persistence",
    "--disable-slash-commands",
]


class ConnectorError(Exception):
    """A failed attempt; `outcome` is error | timeout | invalid_json | schema_invalid."""

    def __init__(self, outcome: str, detail: str):
        super().__init__(detail)
        self.outcome = outcome


class RouterError(Exception):
    def __init__(self, job: str, attempts: list[dict]):
        super().__init__(f"every endpoint failed for job {job!r} ({len(attempts)} attempts logged)")
        self.attempts = attempts


def load_settings(path: str | Path | None = None) -> dict:
    with open(path or DEFAULT_SETTINGS, "rb") as f:
        return tomllib.load(f)


def parse_json_text(text: str) -> object:
    """JSON from a model's text answer; a ```json fence around it is allowed."""
    t = text.strip()
    if m := re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", t, re.S):
        t = m.group(1)
    try:
        return json.loads(t)
    except json.JSONDecodeError as e:
        raise ConnectorError("invalid_json", f"model output is not JSON: {e}") from e


# --- connectors: call(endpoint, system, prompt, schema) -> (output, usage | None) ---

def claude_command(endpoint: dict, system: str, schema: dict) -> list[str]:
    """The argv; the prompt goes on stdin (one argv string is capped at 128 KB on Linux)."""
    return ["claude", "-p", *CLAUDE_FLAGS, "--system-prompt", system,
            "--model", endpoint["model"], "--json-schema", json.dumps(schema)]


def parse_claude_envelope(stdout: str) -> tuple[object, dict | None]:
    """The CLI's JSON envelope is not the job output: take `structured_output` from a successful result."""
    try:
        env = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise ConnectorError("invalid_json", f"claude CLI output is not JSON: {e}") from e
    if env.get("is_error") or env.get("subtype") != "success":
        raise ConnectorError("error", f"claude CLI result: subtype={env.get('subtype')!r} is_error={env.get('is_error')!r}")
    if env.get("structured_output") is None:
        raise ConnectorError("invalid_json", "claude CLI returned no structured_output")
    u = env.get("usage") or {}
    usage = {"input_tokens": sum(u.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens",
                                                         "cache_read_input_tokens")),
             "output_tokens": u.get("output_tokens")} if u else None
    return env["structured_output"], usage


def claude_cli(endpoint: dict, system: str, prompt: str, schema: dict, **_) -> tuple[object, dict | None]:
    with tempfile.TemporaryDirectory(prefix="steer-claude-") as cwd:  # an empty folder: nothing to read or touch
        try:
            run = subprocess.run(claude_command(endpoint, system, schema), input=prompt, cwd=cwd,
                                 capture_output=True, text=True, timeout=endpoint.get("timeout", 300))
        except subprocess.TimeoutExpired as e:
            raise ConnectorError("timeout", f"claude CLI timed out after {e.timeout} s") from e
        except FileNotFoundError as e:
            raise ConnectorError("error", "claude CLI not found on PATH") from e
    if run.returncode != 0 and not run.stdout.strip():
        raise ConnectorError("error", f"claude CLI exit code {run.returncode}: {run.stderr.strip()[:300]}")
    return parse_claude_envelope(run.stdout)


def _post(client: httpx.Client, url: str, headers: dict, body: dict, timeout: float) -> dict:
    try:
        r = client.post(url, headers=headers | {"User-Agent": USER_AGENT}, json=body, timeout=timeout)
    except httpx.TimeoutException as e:
        raise ConnectorError("timeout", f"{url} timed out") from e
    except httpx.HTTPError as e:
        raise ConnectorError("error", f"cannot reach {url}: {type(e).__name__}") from e
    if r.status_code != 200:
        raise ConnectorError("error", f"{url} answered HTTP {r.status_code}")
    return r.json()


def _schema_instruction(system: str, schema: dict) -> str:
    return f"{system}\n\nReturn only a JSON value matching this JSON Schema, with no other text:\n{json.dumps(schema)}"


def anthropic_api(endpoint: dict, system: str, prompt: str, schema: dict, *, key: str,
                  client: httpx.Client, **_) -> tuple[object, dict | None]:
    data = _post(client, endpoint.get("base_url", "https://api.anthropic.com") + "/v1/messages",
                 {"x-api-key": key, "anthropic-version": "2023-06-01"},
                 {"model": endpoint["model"], "max_tokens": endpoint.get("max_tokens", 8000),
                  "system": _schema_instruction(system, schema), "messages": [{"role": "user", "content": prompt}]},
                 endpoint.get("timeout", 300))
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    u = data.get("usage")
    usage = {"input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens")} if u else None
    return parse_json_text(text), usage


def openai_compatible(endpoint: dict, system: str, prompt: str, schema: dict, *, key: str,
                      client: httpx.Client, run_id: str, **_) -> tuple[object, dict | None]:
    data = _post(client, endpoint["base_url"].rstrip("/") + "/chat/completions",
                 {"Authorization": f"Bearer {key}", "x-opencode-session": run_id},
                 {"model": endpoint["model"],
                  "messages": [{"role": "system", "content": _schema_instruction(system, schema)},
                               {"role": "user", "content": prompt}]},
                 endpoint.get("timeout", 300))
    try:
        text = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as e:
        raise ConnectorError("invalid_json", "response has no choices[0].message.content") from e
    u = data.get("usage")
    usage = {"input_tokens": u.get("prompt_tokens"), "output_tokens": u.get("completion_tokens")} if u else None
    return parse_json_text(text), usage


CONNECTORS = {"claude_cli": claude_cli, "anthropic_api": anthropic_api, "openai_compatible": openai_compatible}


class Router:
    def __init__(self, settings: dict, connectors: dict | None = None, client: httpx.Client | None = None,
                 env: dict | None = None):
        self.settings = settings
        self.connectors = connectors or CONNECTORS
        self.client = client or httpx.Client()
        self.env = os.environ if env is None else env
        self.run_id = uuid.uuid4().hex  # one stable session id per run (sent as x-opencode-session)
        self.log: list[dict] = []

    def call(self, job: str, system: str, prompt: str, schema: dict) -> dict:
        """The first schema-valid output along the job's chain: {output, endpoint, model, usage}."""
        attempts = []
        for name in self.settings["jobs"][job]["chain"]:
            ep = self.settings["endpoints"][name]
            base = {"job": job, "endpoint": name, "connector": ep["connector"], "model": ep.get("model")}
            key = None
            if ep.get("key_env"):
                key = self.env.get(ep["key_env"])
                if not key:
                    attempts.append(base | {"attempt": 0, "outcome": "skipped_no_key", "latency_ms": 0,
                                            "tokens": None, "detail": f"{ep['key_env']} is not set"})
                    continue
            for attempt in (1, 2):  # one call, one retry on the same endpoint
                t0 = time.perf_counter()
                entry = base | {"attempt": attempt, "tokens": None}
                try:
                    output, usage = self.connectors[ep["connector"]](
                        ep, system, prompt, schema, key=key, client=self.client, run_id=self.run_id)
                    try:
                        jsonschema.validate(output, schema)
                    except jsonschema.ValidationError as e:
                        raise ConnectorError("schema_invalid", f"output does not match the {job} schema: {e.message}")
                    entry |= {"outcome": "ok", "tokens": usage, "detail": ""}
                    entry["latency_ms"] = round((time.perf_counter() - t0) * 1000)
                    attempts.append(entry)
                    self.log += attempts
                    return {"output": output, "endpoint": name, "model": ep.get("model"), "usage": usage}
                except ConnectorError as e:
                    entry |= {"outcome": e.outcome, "detail": self._clean(str(e), key)}
                except Exception as e:  # a connector bug must not stop the chain, but it is logged
                    entry |= {"outcome": "error", "detail": self._clean(f"{type(e).__name__}: {e}", key)}
                entry["latency_ms"] = round((time.perf_counter() - t0) * 1000)
                attempts.append(entry)
        self.log += attempts
        raise RouterError(job, attempts)

    @staticmethod
    def _clean(text: str, key: str | None) -> str:
        """Log text with any credential removed."""
        return text.replace(key, "[redacted]") if key else text
