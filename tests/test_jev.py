"""Unit tests for jev_decision primitives, fallbacks, live mock server, and harness guardrails."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import pytest

from jev_decision import (
    CalibrationTier,
    ChoiceQuestion,
    JevClient,
    NoulQuestion,
    ScoreQuestion,
    classify_memory_relation,
    guard_bash_command,
    prune_tool_output,
    verify_turn_completion,
)


def test_primitives_serialization():
    nq = NoulQuestion(id="q1", prompt="Is this safe?")
    cq = ChoiceQuestion(id="q2", prompt="Choose category", options=["a", "b", "c"])
    sq = ScoreQuestion(id="q3", prompt="Rate relevance", scale=[0, 1, 2, 3, 4])

    assert nq.to_dict() == {"id": "q1", "type": "noul", "prompt": "Is this safe?"}
    assert cq.to_dict() == {"id": "q2", "type": "choice", "prompt": "Choose category", "options": ["a", "b", "c"]}
    assert sq.to_dict() == {"id": "q3", "type": "score", "prompt": "Rate relevance", "scale": [0, 1, 2, 3, 4]}


def test_offline_fallback_safe_bash():
    client = JevClient(offline_mode=True)

    # Safe command: git status
    res = guard_bash_command("git status", cwd="/repo", client=client)
    assert res["allow_auto"] is True
    assert res["escalate_to_user"] is False
    assert res["safety_probability"] >= 0.95
    assert res["is_fallback"] is True

    # Safe command: pytest
    res2 = guard_bash_command("pytest tests/test_core.py", client=client)
    assert res2["allow_auto"] is True
    assert res2["safety_probability"] >= 0.95


def test_offline_fallback_destructive_bash():
    client = JevClient(offline_mode=True)

    # Obvious destructive command: rm -rf /
    res = guard_bash_command("rm -rf / --no-preserve-root", client=client)
    assert res["allow_auto"] is False
    assert res["escalate_to_user"] is True
    assert res["safety_probability"] <= 0.05

    # Force push
    res_push = guard_bash_command("git push origin main --force", client=client)
    assert res_push["allow_auto"] is False
    assert res_push["escalate_to_user"] is True


def test_context_pruning():
    client = JevClient(offline_mode=True)

    # 200 lines of repetitive output
    lines = [f"Passing test item {i}: ok" for i in range(200)]
    raw_output = "\n".join(lines)

    pruned, stats = prune_tool_output(raw_output, current_goal="fix auth bug", client=client, max_retained_lines=50)
    assert stats["pruned"] is True
    assert stats["saved_lines"] > 0
    assert "lines of boilerplate/passing output omitted" in pruned


def test_verification_completion():
    client = JevClient(offline_mode=True)

    # State with failure
    res_fail = verify_turn_completion(
        goal="Fix issue #123",
        recent_actions="edited file.py",
        last_output="AssertionError: 2 != 3",
        client=client,
    )
    assert res_fail["is_complete"] is False

    # State with all checks passed
    res_ok = verify_turn_completion(
        goal="Fix issue #123",
        recent_actions="ran test",
        last_output="100% green, 45 passed in 0.2s",
        client=client,
    )
    assert res_ok["is_complete"] is True


def test_memory_relation_classification():
    client = JevClient(offline_mode=True)

    # Contradiction with negation
    rel1 = classify_memory_relation(
        new_fact="Do not use Postgres, use SQLite now",
        existing_memory="Use Postgres for primary database",
        client=client,
    )
    assert "contradict" in rel1

    # Reinforcement
    rel2 = classify_memory_relation(
        new_fact="Engraphis stores memories in SQLite tables",
        existing_memory="SQLite database is used for local memory storage in Engraphis",
        client=client,
    )
    assert "reinforce" in rel2


class MockJevHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_len = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_len).decode("utf-8"))

        # Verify Jev contract
        assert "state" in body
        assert "questions" in body

        response_decisions = {}
        for q in body["questions"]:
            q_id = q["id"]
            q_type = q["type"]
            if q_type == "noul":
                response_decisions[q_id] = {
                    "type": "noul",
                    "probability": 0.98,
                    "confidence": 0.96,
                }
            elif q_type == "choice":
                opts = q.get("options", ["opt1"])
                response_decisions[q_id] = {
                    "type": "choice",
                    "selected": opts[0],
                    "probabilities": {opts[0]: 0.95},
                    "confidence": 0.95,
                }
            elif q_type == "score":
                response_decisions[q_id] = {
                    "type": "score",
                    "score": 4,
                    "probabilities": {"4": 0.9},
                    "confidence": 0.9,
                }

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"decisions": response_decisions}).encode("utf-8"))

    def log_message(self, format, *args):
        pass  # Quiet logging in tests


def test_mock_live_jev_api():
    server = HTTPServer(("127.0.0.1", 0), MockJevHandler)
    port = server.server_port
    thread = threading.Thread(target=server.handle_request)
    thread.daemon = True
    thread.start()

    client = JevClient(
        api_key="test-key-123",
        base_url=f"http://127.0.0.1:{port}/v1/decide",
        offline_mode=False,
    )

    questions = [
        NoulQuestion("safe_q", "Is command safe?"),
        ChoiceQuestion("cat_q", "Category", options=["safe", "destructive"]),
        ScoreQuestion("rel_q", "Relevance", scale=[0, 1, 2, 3, 4]),
    ]

    batch = client.evaluate("git status", questions)
    assert batch.is_fallback is False
    assert batch.latency_ms > 0
    assert batch.get_noul("safe_q").probability == 0.98
    assert batch.get_choice("cat_q").selected == "safe"
    assert batch.get_score("rel_q").score == 4

    server.server_close()
