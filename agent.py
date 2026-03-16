import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv


MAX_TOOL_CALLS = 10
PROJECT_ROOT = Path(__file__).resolve().parent


def fail(message: str, exit_code: int = 1) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(exit_code)


def resolve_safe_path(relative_path: str) -> Path:
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ValueError("Error: path must be a non-empty string")

    candidate = (PROJECT_ROOT / relative_path).resolve()
    try:
        candidate.relative_to(PROJECT_ROOT)
    except ValueError as e:
        raise ValueError("Error: access outside project root is not allowed") from e
    return candidate


def read_file(path: str) -> str:
    try:
        target = resolve_safe_path(path)
    except ValueError as e:
        return str(e)

    if not target.exists():
        return f"Error: file does not exist: {path}"
    if not target.is_file():
        return f"Error: not a file: {path}"

    try:
        return target.read_text(encoding="utf-8")
    except Exception as e:
        return f"Error reading file {path}: {e}"


def list_files(path: str) -> str:
    try:
        target = resolve_safe_path(path)
    except ValueError as e:
        return str(e)

    if not target.exists():
        return f"Error: path does not exist: {path}"
    if not target.is_dir():
        return f"Error: not a directory: {path}"

    try:
        entries = sorted(
            entry.name + ("/" if entry.is_dir() else "") for entry in target.iterdir()
        )
        return "\n".join(entries)
    except Exception as e:
        return f"Error listing directory {path}: {e}"


def get_tool_schemas() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file from the repository using a path relative to the project root.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Relative file path from the project root.",
                        }
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_files",
                "description": "List files and directories at a path relative to the project root.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Relative directory path from the project root.",
                        }
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
        },
    ]


def execute_tool(tool_name: str, args: dict) -> str:
    if tool_name == "read_file":
        return read_file(args.get("path", ""))
    if tool_name == "list_files":
        return list_files(args.get("path", ""))
    return f"Error: unknown tool: {tool_name}"


def extract_message_text(message: dict) -> str:
    content = message.get("content")

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text", "")
                if text:
                    parts.append(text)
        return "\n".join(parts).strip()

    return ""


def parse_final_answer(text: str) -> dict:
    fallback = {
        "answer": text.strip(),
        "source": "",
    }

    if not text.strip():
        return fallback

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return fallback

    if not isinstance(parsed, dict):
        return fallback

    return {
        "answer": str(parsed.get("answer", "")).strip(),
        "source": str(parsed.get("source", "")).strip(),
    }


def request_chat_completion(
    client: httpx.Client,
    url: str,
    headers: dict,
    model: str,
    messages: list[dict],
    tools: list[dict],
) -> dict:
    payload = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
    }

    response = client.post(url, headers=headers, json=payload)
    response.raise_for_status()
    return response.json()


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
    url = base if base.endswith("/chat/completions") else f"{base}/chat/completions"

    system_prompt = (
        "You are a documentation agent for this repository. "
        "Use list_files to discover relevant wiki files, then use read_file to inspect them. "
        "Answer only from the repository documentation when possible. "
        "When you provide the final answer, respond with valid JSON exactly in this form: "
        '{"answer":"...","source":"wiki/file.md#section-anchor"}. '
        "The source field is required. "
        "If you cannot find a precise section anchor, provide the best available file path source. "
        "Do not invent documentation or source references."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question},
    ]

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    tools = get_tool_schemas()
    tool_calls_log: list[dict] = []
    final_answer = {
        "answer": "",
        "source": "",
    }

    try:
        with httpx.Client(timeout=30.0) as client:
            while True:
                if len(tool_calls_log) >= MAX_TOOL_CALLS:
                    final_answer = {
                        "answer": "I could not finish the search within the 10 tool call limit.",
                        "source": "",
                    }
                    break

                data = request_chat_completion(
                    client=client,
                    url=url,
                    headers=headers,
                    model=model,
                    messages=messages,
                    tools=tools,
                )

                try:
                    message = data["choices"][0]["message"]
                except KeyError, IndexError, TypeError:
                    fail("Invalid response format from LLM API")

                assistant_message = {
                    "role": "assistant",
                    "content": message.get("content"),
                }

                tool_calls = message.get("tool_calls") or []
                if tool_calls:
                    assistant_message["tool_calls"] = tool_calls

                messages.append(assistant_message)

                if not tool_calls:
                    final_text = extract_message_text(message)
                    final_answer = parse_final_answer(final_text)
                    if not final_answer["answer"]:
                        final_answer["answer"] = final_text
                    break

                for tool_call in tool_calls:
                    if len(tool_calls_log) >= MAX_TOOL_CALLS:
                        final_answer = {
                            "answer": "I could not finish the search within the 10 tool call limit.",
                            "source": "",
                        }
                        break

                    function_data = tool_call.get("function", {})
                    tool_name = function_data.get("name", "")
                    raw_arguments = function_data.get("arguments", "{}")

                    try:
                        args = json.loads(raw_arguments)
                        if not isinstance(args, dict):
                            args = {}
                    except json.JSONDecodeError:
                        args = {}

                    result = execute_tool(tool_name, args)

                    tool_calls_log.append(
                        {
                            "tool": tool_name,
                            "args": args,
                            "result": result,
                        }
                    )

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.get("id", ""),
                            "content": result,
                        }
                    )

                if final_answer["answer"]:
                    break

    except httpx.TimeoutException:
        fail("LLM request timed out")
    except httpx.HTTPStatusError as e:
        body = e.response.text.strip()
        fail(f"LLM API error {e.response.status_code}: {body}")
    except Exception as e:
        fail(f"Unexpected error: {e}")

    result = {
        "answer": final_answer.get("answer", ""),
        "source": final_answer.get("source", ""),
        "tool_calls": tool_calls_log,
    }

    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
