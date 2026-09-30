"""Escalate-only harness hook adapter; no provider calls and no real clients."""
import io
import json

import pytest

from jev_decision import cli, hooks
from jev_decision.client import JevClient
from jev_decision.harness_guards import GUARD_CATEGORIES, guard_bash_command
from jev_decision.primitives import ChoiceDecision, DecisionBatch, NoulDecision
from jev_decision.runtime import RuntimeConfig

PAYLOADS = {
    "claude-code": {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": "/repo",
                    "permission_mode": "default", "tool_input": {"command": "rm -rf build/"}},
    "command-code": {"hook_event_name": "PreToolUse", "tool_name": "shell_command", "cwd": "/repo",
                     "permission_mode": "bypass", "tool_input": {"command": "rm", "args": ["-rf", "build dir/"],
                                                                 "cwd": "/repo/pkg"}},
    "codex": {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": "/repo",
              "permission_mode": "bypassPermissions", "tool_input": {"command": ["bash", "-lc", "rm -rf build/"]}},
    "cursor": {"hook_event_name": "beforeShellExecution", "command": "rm -rf build/", "cwd": "/repo"},
    "gemini-cli": {"hook_event_name": "BeforeTool", "tool_name": "run_shell_command", "cwd": "/repo",
                   "permission_mode": "yolo", "tool_input": {"command": "rm -rf build/", "directory": "/repo"}},
}


class FakeGuardClient:
    def __init__(self, destructive=0.95, risk=0.9, status="ok"):
        self.calls, self.destructive, self.risk, self.status = [], destructive, risk, status

    def evaluate(self, state, questions):
        self.calls.append((state, questions))
        if self.status != "ok":
            return DecisionBatch(status=self.status, error_code="timeout")
        probabilities = {name: (1 - self.destructive) / 4 for name in GUARD_CATEGORIES}
        probabilities["destructive_or_sensitive"] = self.destructive
        selected = max(probabilities, key=probabilities.get)
        return DecisionBatch(status="ok", source="provider", resolved_model="jev-1.13.0", decisions={
            "category": ChoiceDecision("category", selected, probabilities, 0.9),
            "risk": NoulDecision("risk", self.risk)})


def test_payloads_are_normalized_for_every_harness():
    assert hooks.extract_command("claude-code", PAYLOADS["claude-code"]) == ("rm -rf build/", "/repo")
    assert hooks.extract_command("command-code", PAYLOADS["command-code"]) == ("rm -rf 'build dir/'", "/repo/pkg")
    assert hooks.extract_command("codex", PAYLOADS["codex"]) == ("bash -lc 'rm -rf build/'", "/repo")
    assert hooks.extract_command("cursor", PAYLOADS["cursor"]) == ("rm -rf build/", "/repo")
    windows = dict(PAYLOADS["claude-code"], tool_name="PowerShell", tool_input={"command": "Remove-Item -Recurse build"})
    assert hooks.extract_command("claude-code", windows) == ("Remove-Item -Recurse build", "/repo")
    assert hooks.extract_command("gemini-cli", PAYLOADS["gemini-cli"]) == ("rm -rf build/", "/repo")
    for harness, payload in (("claude-code", {"tool_name": "Edit", "tool_input": {"file_path": "x"}}),
                             ("codex", {"tool_name": "apply_patch", "tool_input": {"command": "x"}}),
                             ("command-code", {"tool_name": "shell_output", "tool_input": {"command": "x"}}),
                             ("cursor", {"command": "   "}), ("claude-code", ["not", "an", "object"])):
        with pytest.raises(hooks.HookInputError):
            hooks.extract_command(harness, payload)


@pytest.mark.parametrize("command", ["ls -la", "git status", "git log --oneline -5", "cat README.md",
                                     "grep -n TODO src/app.py", "pwd", "/usr/bin/wc -l notes.txt"])
def test_simple_inspection_needs_no_semantic_check(command):
    assert hooks.is_plainly_read_only(command)


@pytest.mark.parametrize("command", ["rm -rf build", "cat .env", "cat ~/.ssh/id_rsa", "ls | sh",
                                     "echo $(whoami)", "git push --force", "git -C other status",
                                     "cat keys/server.pem", "git diff --output=patch.txt", "ls > out",
                                     "find . -delete", "sed -i s/a/b/ file", "env", "cat 'unterminated"])
def test_anything_else_is_checked(command):
    assert not hooks.is_plainly_read_only(command)


@pytest.mark.parametrize("harness", sorted(hooks.ASK_CAPABLE))
def test_ask_capable_harnesses_escalate_to_the_native_prompt(harness):
    client = FakeGuardClient()
    decision = hooks.evaluate_hook(harness, PAYLOADS[harness], client=client)
    text = json.dumps(decision)
    assert '"ask"' in text and '"allow"' not in text and "p=0.95" in text
    state, _ = client.calls[0]
    assert state == {"command": "rm -rf build/", "cwd": "/repo"}


@pytest.mark.parametrize("harness", ["command-code", "codex", "gemini-cli"])
def test_deny_only_harnesses_block_only_unattended_sessions_by_default(harness):
    decision = hooks.evaluate_hook(harness, PAYLOADS[harness], client=FakeGuardClient())
    assert "deny" in json.dumps(decision) and "allow" not in json.dumps(decision)
    attended = dict(PAYLOADS[harness], permission_mode="default")
    client = FakeGuardClient()
    assert hooks.evaluate_hook(harness, attended, client=client) is None and client.calls == []
    assert "deny" in json.dumps(hooks.evaluate_hook(harness, attended, client=FakeGuardClient(), when="always"))


@pytest.mark.parametrize("client", [FakeGuardClient(destructive=0.3, risk=0.4), FakeGuardClient(status="unavailable")])
def test_low_risk_or_unavailable_advice_leaves_the_harness_unchanged(client):
    assert hooks.evaluate_hook("claude-code", PAYLOADS["claude-code"], client=client) is None


def test_read_only_commands_make_no_request():
    client = FakeGuardClient()
    payload = dict(PAYLOADS["claude-code"], tool_input={"command": "git status"})
    assert hooks.evaluate_hook("claude-code", payload, client=client) is None and client.calls == []


def test_run_hook_fails_open_and_can_be_disabled():
    raw = json.dumps(PAYLOADS["claude-code"]).encode()
    assert '"ask"' in hooks.run_hook("claude-code", raw, client=FakeGuardClient(), environ={})
    assert hooks.run_hook("claude-code", raw, client=FakeGuardClient(), environ={"JEV_HOOK": "off"}) == ""
    assert hooks.run_hook("claude-code", b"{not json", client=FakeGuardClient(), environ={}) == ""
    assert hooks.run_hook("claude-code", b" " * (hooks.MAX_HOOK_INPUT_BYTES + 1), client=FakeGuardClient(),
                          environ={}) == ""

    class Broken:
        def evaluate(self, *args, **kwargs):
            raise RuntimeError("synthetic failure")

    assert hooks.run_hook("claude-code", raw, client=Broken(), environ={}) == ""
    assert hooks.run_hook("claude-code", raw, client=FakeGuardClient(), environ={"JEV_HOOK_THRESHOLD": "0.99"}) == ""


@pytest.mark.parametrize("argv", [["hook", "run", "claude-code"], ["hook", "run", "unknown-harness"],
                                  ["--runtime-home", "relative", "hook", "run", "cursor"],
                                  ["hook", "run", "claude-code", "--threshold", "not-a-number"]])
def test_cli_hook_never_blocks_on_local_failure(argv, monkeypatch, capsys):
    # A fresh installation has no key: the hook must exit 0 without output,
    # because exit code 2 means "block" to several harnesses.
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(json.dumps(PAYLOADS["claude-code"]).encode())))
    assert cli.main(argv) == 0
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_cli_hook_prints_the_harness_decision(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(json.dumps(PAYLOADS["cursor"]).encode())))
    monkeypatch.setattr(hooks, "evaluate_hook", lambda harness, payload, **kw: hooks._format(harness, "flagged"))
    assert cli.main(["hook", "run", "cursor"]) == 0
    assert json.loads(capsys.readouterr().out) == {"permission": "ask", "user_message": "flagged",
                                                    "agent_message": "flagged"}


@pytest.mark.parametrize("harness", hooks.HOOK_HARNESSES)
def test_config_fragments_bind_the_runtime_and_never_approve(harness, capsys):
    assert cli.main(["hook", "config", harness]) == 0
    result = json.loads(capsys.readouterr().out)
    text = json.dumps(result["fragment"])
    assert str(RuntimeConfig.load().home).replace("\\", "\\\\") in text and "hook" in text
    assert result["decision"] in ("ask", "deny") and "allow" not in text
    if harness == "claude-code":
        entry = result["fragment"]["hooks"]["PreToolUse"][0]
        assert entry["matcher"] == "Bash|PowerShell" and entry["hooks"][0]["args"][-3:] == ["hook", "run", "claude-code"]
    if harness == "command-code":
        assert result["fragment"]["hooks"]["PreToolUse"][0]["matcher"] == "^shell$"


def test_guard_sends_descriptive_criteria_and_reports_category_probabilities(tmp_path):
    requests = []

    def transport(request, timeout_s, limit):
        body = json.loads(request.data)
        requests.append(body)
        probabilities = {name: 0.05 for name in body["questions"]["category"]["criteria"]}
        probabilities["destructive_or_sensitive"] = 0.8
        answers = {"category": {"type": "choice", "choice": "destructive_or_sensitive", "confidence": 0.8,
                                "probabilities": probabilities}, "risk": {"type": "noul", "noul": 0.7}}
        return 200, json.dumps({"model": body["model"], "answers": answers,
                                "usage": {"input_tokens": 90, "output_tokens": 0}}).encode()

    client = JevClient(api_key="synthetic-key", runtime=RuntimeConfig(home=tmp_path, enabled=True), transport=transport)
    result = guard_bash_command("git push --force origin main", cwd="/repo", client=client)
    criteria = requests[0]["questions"]["category"]["criteria"]
    assert criteria == GUARD_CATEGORIES and all(len(text) > 20 for text in criteria.values())
    assert set(requests[0]["questions"]["risk"]["criteria"]) == {"true", "false"}
    assert result["status"] == "ok" and result["risk_category"] == "destructive_or_sensitive"
    assert result["category_probabilities"]["destructive_or_sensitive"] == 0.8
    assert result["risk_probability"] == 0.7 and result["permission_authority"] == "native_harness"
