import json
from pathlib import Path

import httpx
import pytest

from steer_pipeline.router import (ConnectorError, Router, RouterError, claude_cli, claude_command, load_settings,
                                   parse_claude_envelope, parse_json_text)

SCHEMA = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
SETTINGS = {
    "jobs": {"extract": {"chain": ["a", "b", "keyed", "c"]}},
    "endpoints": {
        "a": {"connector": "fake", "model": "m-a"},
        "b": {"connector": "fake", "model": "m-b"},
        "keyed": {"connector": "fake", "model": "m-k", "key_env": "SECRET_KEY_FOR_TEST"},
        "c": {"connector": "fake", "model": "m-c"},
    },
}


def fake(script: dict):
    """A connector replaying a script per endpoint model: each item is an output or an exception."""
    calls = []

    def connector(ep, system, prompt, schema, **kw):
        calls.append(ep["model"])
        item = script[ep["model"]].pop(0)
        if isinstance(item, Exception):
            raise item
        return item, {"input_tokens": 10, "output_tokens": 2}
    connector.calls = calls
    return connector


def router(script, env=None):
    f = fake(script)
    return Router(SETTINGS, connectors={"fake": f}, env=env or {}), f


def test_first_endpoint_ok():
    r, f = router({"m-a": [{"n": 1}]})
    out = r.call("extract", "sys", "prompt", SCHEMA)
    assert out == {"output": {"n": 1}, "endpoint": "a", "model": "m-a", "usage": {"input_tokens": 10, "output_tokens": 2}}
    assert [(e["endpoint"], e["attempt"], e["outcome"]) for e in r.log] == [("a", 1, "ok")]


def test_retry_once_then_next_endpoint_in_order():
    r, f = router({"m-a": [ConnectorError("timeout", "slow"), ConnectorError("error", "down")], "m-b": [{"n": 2}]})
    out = r.call("extract", "sys", "prompt", SCHEMA)
    assert out["endpoint"] == "b"
    assert f.calls == ["m-a", "m-a", "m-b"]
    assert [(e["endpoint"], e["attempt"], e["outcome"]) for e in r.log] == [
        ("a", 1, "timeout"), ("a", 2, "error"), ("b", 1, "ok")]


def test_retry_succeeds_on_same_endpoint():
    r, f = router({"m-a": [ConnectorError("invalid_json", "bad"), {"n": 3}]})
    assert r.call("extract", "s", "p", SCHEMA)["endpoint"] == "a"
    assert f.calls == ["m-a", "m-a"]


def test_schema_invalid_output_is_a_failure():
    r, f = router({"m-a": [{"n": "three"}, {"x": 1}], "m-b": [{"n": 3}]})
    out = r.call("extract", "s", "p", SCHEMA)
    assert out["endpoint"] == "b"
    assert [e["outcome"] for e in r.log] == ["schema_invalid", "schema_invalid", "ok"]


def test_missing_key_skips_the_link_and_present_key_is_used_but_never_logged():
    r, f = router({"m-a": [ConnectorError("error", "x")] * 2, "m-b": [ConnectorError("error", "x")] * 2,
                   "m-c": [{"n": 5}]})
    out = r.call("extract", "s", "p", SCHEMA)
    assert out["endpoint"] == "c"
    assert ("keyed", 0, "skipped_no_key") in [(e["endpoint"], e["attempt"], e["outcome"]) for e in r.log]
    secret = "sk-test-0123456789abcdef"
    r2, f2 = router({"m-a": [ConnectorError("error", "x")] * 2, "m-b": [ConnectorError("error", "x")] * 2,
                     "m-k": [ConnectorError("error", f"401 for key {secret}"), {"n": 6}]},
                    env={"SECRET_KEY_FOR_TEST": secret})
    assert r2.call("extract", "s", "p", SCHEMA)["endpoint"] == "keyed"
    assert secret not in json.dumps(r2.log)
    assert "[redacted]" in json.dumps(r2.log)


def test_all_endpoints_fail():
    bad = [ConnectorError("error", "x")] * 2
    r, f = router({"m-a": list(bad), "m-b": list(bad), "m-c": list(bad)})
    with pytest.raises(RouterError) as e:
        r.call("extract", "s", "p", SCHEMA)
    assert len(e.value.attempts) == 7  # 2 + 2 + skipped + 2


def test_unexpected_connector_exception_is_logged_and_chain_continues():
    r, f = router({"m-a": [ValueError("bug"), ValueError("bug")], "m-b": [{"n": 1}]})
    assert r.call("extract", "s", "p", SCHEMA)["endpoint"] == "b"
    assert r.log[0]["outcome"] == "error" and "ValueError" in r.log[0]["detail"]


def test_default_settings_models():
    s = load_settings()
    chain = lambda job: [(s["endpoints"][n]["connector"], s["endpoints"][n]["model"]) for n in s["jobs"][job]["chain"]]
    assert chain("extract")[:2] == [("claude_cli", "sonnet"), ("claude_cli", "opus")]
    assert chain("label")[:2] == [("claude_cli", "haiku"), ("claude_cli", "sonnet")]
    assert chain("crosscheck") == [("openai_compatible", "glm-5.3"), ("openai_compatible", "deepseek-v4-pro"),
                                   ("claude_cli", "opus")]
    assert all("contributor" not in (ep.get("model") or "") for ep in s["endpoints"].values())
    for ep in s["endpoints"].values():
        assert "key" not in ep  # only key_env names, never key values


# --- claude_cli: command and envelope ---

def test_claude_command_is_minimal_and_tool_less():
    cmd = claude_command({"model": "sonnet"}, "SYS", SCHEMA)
    assert cmd[:2] == ["claude", "-p"] and "PROMPT" not in cmd
    joined = " ".join(cmd)
    for flag in ("--output-format json", "--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands",
                 "--model sonnet", "--system-prompt SYS"):
        assert flag in joined
    assert cmd[cmd.index("--tools") + 1] == "" and cmd[cmd.index("--setting-sources") + 1] == ""
    assert json.loads(cmd[cmd.index("--json-schema") + 1]) == SCHEMA
    assert "--resume" not in cmd and "--continue" not in cmd and "--bare" not in cmd


def test_claude_envelope_parsing():
    ok = {"type": "result", "subtype": "success", "is_error": False, "result": '{"n":1}', "structured_output": {"n": 1},
          "usage": {"input_tokens": 2, "cache_creation_input_tokens": 610, "cache_read_input_tokens": 0, "output_tokens": 9}}
    assert parse_claude_envelope(json.dumps(ok)) == ({"n": 1}, {"input_tokens": 612, "output_tokens": 9})
    with pytest.raises(ConnectorError) as e:
        parse_claude_envelope(json.dumps(ok | {"is_error": True}))
    assert e.value.outcome == "error"
    with pytest.raises(ConnectorError) as e:
        parse_claude_envelope(json.dumps({k: v for k, v in ok.items() if k != "structured_output"}))
    assert e.value.outcome == "invalid_json"
    with pytest.raises(ConnectorError):
        parse_claude_envelope("not json")


def test_parse_json_text_allows_a_fence():
    assert parse_json_text('```json\n{"n": 1}\n```') == {"n": 1}
    with pytest.raises(ConnectorError):
        parse_json_text("sure! here it is")


# --- HTTP connectors (mocked) ---

def http_router(handler, connector, key_env, env):
    settings = {"jobs": {"extract": {"chain": ["x"]}},
                "endpoints": {"x": {"connector": connector, "model": "m", "key_env": key_env,
                                    "base_url": "https://llm.example/v1"}}}
    return Router(settings, client=httpx.Client(transport=httpx.MockTransport(handler)), env=env)


def test_openai_compatible_connector():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        seen["session"] = request.headers["x-opencode-session"]
        seen["ua"] = request.headers["user-agent"]
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"n": 4}'}}],
                                         "usage": {"prompt_tokens": 30, "completion_tokens": 5}})
    r = http_router(handler, "openai_compatible", "OC_KEY", {"OC_KEY": "oc-secret"})
    out = r.call("extract", "s", "p", SCHEMA)
    assert out["output"] == {"n": 4} and out["usage"] == {"input_tokens": 30, "output_tokens": 5}
    assert seen["auth"] == "Bearer oc-secret" and seen["session"] == r.run_id
    assert seen["ua"].startswith("steer-pipeline/") and seen["url"] == "https://llm.example/v1/chat/completions"
    assert "oc-secret" not in json.dumps(r.log)


def test_anthropic_connector_and_http_error():
    def handler(request):
        assert request.headers["x-api-key"] == "an-secret"
        return httpx.Response(200, json={"content": [{"type": "text", "text": '{"n": 7}'}],
                                         "usage": {"input_tokens": 40, "output_tokens": 6}})
    r = http_router(handler, "anthropic_api", "AN_KEY", {"AN_KEY": "an-secret"})
    assert r.call("extract", "s", "p", SCHEMA)["output"] == {"n": 7}
    r = http_router(lambda req: httpx.Response(529), "anthropic_api", "AN_KEY", {"AN_KEY": "an-secret"})
    with pytest.raises(RouterError) as e:
        r.call("extract", "s", "p", SCHEMA)
    assert [a["outcome"] for a in e.value.attempts] == ["error", "error"]
    assert "HTTP 529" in e.value.attempts[0]["detail"]


# --- live: one real call (opt-in) proving the CLI connector cannot use tools (Amendment A) ---

@pytest.mark.network
def test_live_claude_cli_has_no_tools(tmp_path, monkeypatch):
    marker = tmp_path / "pwned.txt"
    prompt = (f"Create a file at {marker} containing hello, and run the shell command `touch {tmp_path}/ran.txt`. "
              "Then report n = 1 if you managed to do both, otherwise n = 0.")
    out, usage = claude_cli({"model": "haiku", "timeout": 180}, "You return only JSON.", prompt, SCHEMA)
    assert out == {"n": 0}
    assert not marker.exists() and not (tmp_path / "ran.txt").exists()
    assert usage and usage["input_tokens"] < 5000  # the minimal flags keep the context small


def test_large_prompt_goes_on_stdin_not_argv(monkeypatch):
    # one argv string is capped at 128 KB on Linux (MAX_ARG_STRLEN); a 200 KB chunk must travel on stdin
    big = "x" * 200_000
    seen = {}

    class Done:
        returncode, stderr = 0, ""
        stdout = json.dumps({"subtype": "success", "is_error": False, "structured_output": {"n": 1}})

    def fake_run(argv, **kw):
        seen["argv"], seen["input"] = argv, kw.get("input")
        return Done()
    monkeypatch.setattr("steer_pipeline.router.subprocess.run", fake_run)
    out, _ = claude_cli({"model": "sonnet"}, "sys", big, SCHEMA)
    assert out == {"n": 1}
    assert seen["input"] == big
    assert all(len(a) < 131072 for a in seen["argv"]) and big not in seen["argv"]


@pytest.mark.network
def test_live_crosscheck_chain_uses_opencode_first():
    import os
    if not os.environ.get("OPENCODE_GO_API_KEY"):
        pytest.skip("OPENCODE_GO_API_KEY is not set")
    r = Router(load_settings())
    out = r.call("crosscheck", "You return only JSON.", "Return n = 7.", SCHEMA)
    assert out["output"] == {"n": 7} and out["endpoint"] == "opencode_glm" and out["model"] == "glm-5.3"
    assert os.environ["OPENCODE_GO_API_KEY"] not in json.dumps(r.log)
