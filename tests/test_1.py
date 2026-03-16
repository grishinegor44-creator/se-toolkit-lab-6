import json
import subprocess


def test_agent_returns_required_json_fields():
    result = subprocess.run(
        ["uv", "run", "agent.py", "What does REST stand for?"],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )

    output = json.loads(result.stdout)

    assert "answer" in output
    assert "tool_calls" in output
    assert isinstance(output["tool_calls"], list)
