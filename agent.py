import json
import os
import sys

import httpx
from dotenv import load_dotenv


def fail(message: str, exit_code: int = 1) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(exit_code)


def main() -> None:
    load_dotenv(".env.agent.secret")

    api_key = os.getenv("LLM_API_KEY")
    api_base = os.getenv("LLM_API_BASE")
    model = os.getenv("LLM_MODEL")

    if len(sys.argv) < 2:
        fail('Usage: uv run agent.py "Your question here"')

    question = sys.argv[1].strip()
    if not question:
        fail("Question must not be empty")

    if not api_key or not api_base or not model:
        fail(
            "Missing LLM configuration: LLM_API_KEY, LLM_API_BASE, and LLM_MODEL are required"
        )

    base = api_base.rstrip("/")
    if base.endswith("/chat/completions"):
        url = base
    else:
        url = f"{base}/chat/completions"

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are a helpful assistant. Answer concisely and clearly.",
            },
            {"role": "user", "content": question},
        ],
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
    except httpx.TimeoutException:
        fail("LLM request timed out")
    except httpx.HTTPStatusError as e:
        body = e.response.text.strip()
        fail(f"LLM API error {e.response.status_code}: {body}")
    except Exception as e:
        fail(f"Unexpected error: {e}")

    try:
        answer = data["choices"][0]["message"]["content"]
    except KeyError, IndexError, TypeError:
        fail("Invalid response format from LLM API")

    result = {"answer": answer, "tool_calls": []}

    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
