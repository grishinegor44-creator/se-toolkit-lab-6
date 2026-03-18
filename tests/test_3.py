import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import agent


def run_agent(
    monkeypatch, tmp_path: Path, question: str, responses: list[dict]
) -> dict:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_API_BASE", "https://example.com/v1")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LMS_API_KEY", "test-lms-key")
    monkeypatch.setenv("AGENT_API_BASE_URL", "http://localhost:42002")

    monkeypatch.setattr(agent, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["agent.py", question])

    response_iter = iter(responses)

    def fake_request_chat_completion(client, url, headers, model, messages, tools):
        return next(response_iter)

    monkeypatch.setattr(agent, "request_chat_completion", fake_request_chat_completion)

    class FakeResponse:
        def __init__(self, status_code: int, json_body):
            self.status_code = status_code
            self._json_body = json_body
            self.text = json.dumps(json_body)

        def json(self):
            return self._json_body

    def fake_request(self, method, url, headers=None, json=None):
        if url.endswith("/items/"):
            return FakeResponse(
                200,
                [
                    {"id": 1, "name": "Item 1"},
                    {"id": 2, "name": "Item 2"},
                    {"id": 3, "name": "Item 3"},
                ],
            )
        raise AssertionError(f"Unexpected API request: {method} {url}")

    monkeypatch.setattr(agent.httpx.Client, "request", fake_request)

    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)

    agent.main()

    return json.loads(stdout.getvalue())


def test_framework_question_uses_read_file(monkeypatch, tmp_path: Path):
    backend_dir = tmp_path / "backend"
    backend_dir.mkdir()
    (backend_dir / "main.py").write_text(
        "from fastapi import FastAPI\n\napp = FastAPI()\n",
        encoding="utf-8",
    )

    responses = [
        {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "read_file",
                                    "arguments": json.dumps(
                                        {"path": "backend/main.py"}
                                    ),
                                },
                            }
                        ],
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "answer": "The backend uses FastAPI.",
                                "source": "backend/main.py",
                            }
                        )
                    }
                }
            ]
        },
    ]

    payload = run_agent(
        monkeypatch,
        tmp_path,
        "What framework does the backend use?",
        responses,
    )

    read_calls = [
        call
        for call in payload.get("tool_calls", [])
        if call.get("tool") == "read_file"
    ]

    assert payload["answer"].strip(), "Answer must not be empty"
    assert read_calls, (
        "Expected the agent to use read_file for a static system fact question"
    )

    first_call = read_calls[0]
    assert first_call["args"] == {"path": "backend/main.py"}
    assert "FastAPI" in first_call["result"]


def test_item_count_question_uses_query_api(monkeypatch, tmp_path: Path):
    responses = [
        {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "query_api",
                                    "arguments": json.dumps(
                                        {"method": "GET", "path": "/items/"}
                                    ),
                                },
                            }
                        ],
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "answer": "There are 3 items in the database.",
                                "source": "",
                            }
                        )
                    }
                }
            ]
        },
    ]

    payload = run_agent(
        monkeypatch,
        tmp_path,
        "How many items are in the database?",
        responses,
    )

    api_calls = [
        call
        for call in payload.get("tool_calls", [])
        if call.get("tool") == "query_api"
    ]

    assert payload["answer"].strip(), "Answer must not be empty"
    assert api_calls, (
        "Expected the agent to use query_api for a data-dependent question"
    )

    first_call = api_calls[0]
    assert first_call["args"] == {"method": "GET", "path": "/items/"}

    parsed_result = json.loads(first_call["result"])
    assert parsed_result["status_code"] == 200
    assert isinstance(parsed_result["body"], list)
    assert len(parsed_result["body"]) == 3
