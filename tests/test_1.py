import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import agent


def test_agent_returns_required_json_fields(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_API_BASE", "https://example.com/v1")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LMS_API_KEY", "test-lms-key")
    monkeypatch.setenv("AGENT_API_BASE_URL", "http://localhost:42002")

    monkeypatch.setattr(agent, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["agent.py", "What does REST stand for?"])

    def fake_request_chat_completion(client, url, headers, model, messages, tools):
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "answer": "REST stands for Representational State Transfer.",
                                "source": "",
                            }
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(agent, "request_chat_completion", fake_request_chat_completion)

    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)

    agent.main()

    output = json.loads(stdout.getvalue())

    assert "answer" in output
    assert "tool_calls" in output
    assert isinstance(output["tool_calls"], list)
