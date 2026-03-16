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

    monkeypatch.setattr(agent, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["agent.py", question])

    response_iter = iter(responses)

    def fake_request_chat_completion(client, url, headers, model, messages, tools):
        return next(response_iter)

    monkeypatch.setattr(agent, "request_chat_completion", fake_request_chat_completion)

    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)

    agent.main()

    return json.loads(stdout.getvalue())


def test_merge_conflict_question_uses_read_file(monkeypatch, tmp_path: Path):
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    (wiki_dir / "git-workflow.md").write_text(
        "# Git Workflow\n\n"
        "## Resolving merge conflicts\n\n"
        "Edit the conflicting file, choose which changes to keep, then stage and commit.\n",
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
                                        {"path": "wiki/git-workflow.md"}
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
                                "answer": "Edit the conflicting file, choose which changes to keep, then stage and commit.",
                                "source": "wiki/git-workflow.md#resolving-merge-conflicts",
                            }
                        )
                    }
                }
            ]
        },
    ]

    result = run_agent(
        monkeypatch,
        tmp_path,
        "How do you resolve a merge conflict?",
        responses,
    )

    assert result["answer"]
    assert result["source"] == "wiki/git-workflow.md#resolving-merge-conflicts"
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["tool"] == "read_file"
    assert result["tool_calls"][0]["args"] == {"path": "wiki/git-workflow.md"}
    assert "Resolving merge conflicts" in result["tool_calls"][0]["result"]


def test_wiki_listing_question_uses_list_files(monkeypatch, tmp_path: Path):
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    (wiki_dir / "git-workflow.md").write_text("# Git Workflow\n", encoding="utf-8")
    (wiki_dir / "testing.md").write_text("# Testing\n", encoding="utf-8")

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
                                    "name": "list_files",
                                    "arguments": json.dumps({"path": "wiki"}),
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
                                "answer": "The wiki contains git-workflow.md and testing.md.",
                                "source": "wiki",
                            }
                        )
                    }
                }
            ]
        },
    ]

    result = run_agent(
        monkeypatch,
        tmp_path,
        "What files are in the wiki?",
        responses,
    )

    assert result["answer"]
    assert result["source"] == "wiki"
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["tool"] == "list_files"
    assert result["tool_calls"][0]["args"] == {"path": "wiki"}
    assert "git-workflow.md" in result["tool_calls"][0]["result"]
    assert "testing.md" in result["tool_calls"][0]["result"]
