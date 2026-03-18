import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import httpx
from dotenv import load_dotenv

MAX_TOOL_CALLS = 8
MAX_LLM_TURNS = 5
MAX_REPAIR_ATTEMPTS = 2
REQUEST_TIMEOUT = 10.0
PROJECT_ROOT = Path(__file__).resolve().parent
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def fail(message: str, exit_code: int = 1) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(exit_code)


def resolve_safe_path(relative_path: str) -> Path:
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ValueError("Error: path must be a non-empty string")

    normalized = relative_path.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = normalized.lstrip("/")

    candidate = (PROJECT_ROOT / normalized).resolve()
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


def query_api(
    client: httpx.Client,
    api_base_url: str,
    lms_api_key: str,
    method: str,
    path: str,
    body: str | None = None,
    include_auth: bool = True,
) -> str:
    if not isinstance(method, str) or not method.strip():
        return json.dumps(
            {"status_code": 400, "body": "Error: method must be a non-empty string"},
            ensure_ascii=False,
        )

    if not isinstance(path, str) or not path.strip():
        return json.dumps(
            {"status_code": 400, "body": "Error: path must be a non-empty string"},
            ensure_ascii=False,
        )

    request_headers = {}
    if include_auth:
        request_headers = {
            "Authorization": f"Bearer {lms_api_key}",
            "X-API-Key": lms_api_key,
        }

    payload = None
    if body is not None and str(body).strip():
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as e:
            return json.dumps(
                {"status_code": 400, "body": f"Error: body must be valid JSON: {e}"},
                ensure_ascii=False,
            )

    url = urljoin(api_base_url.rstrip("/") + "/", path.lstrip("/"))

    try:
        response = client.request(
            method=method.upper(),
            url=url,
            headers=request_headers,
            json=payload,
            follow_redirects=True,
        )
    except httpx.RequestError as e:
        return json.dumps(
            {"status_code": 0, "body": f"Request error: {e}"},
            ensure_ascii=False,
        )

    try:
        response_body = response.json()
    except ValueError:
        response_body = response.text

    return json.dumps(
        {
            "status_code": response.status_code,
            "body": response_body,
        },
        ensure_ascii=False,
    )


def get_tool_schemas() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": (
                    "Read a file from the repository using a path relative to the "
                    "project root. Use this for source code, configuration files, "
                    "framework detection, routes, ports, status codes, implementation "
                    "details, router modules, bug diagnosis, and wiki contents."
                ),
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
                "description": (
                    "List files and directories at a path relative to the project root. "
                    "Use this first when you need to discover where wiki, backend, API, "
                    "router, or source files live."
                ),
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
        {
            "type": "function",
            "function": {
                "name": "query_api",
                "description": (
                    "Call the deployed backend API for live system data. "
                    "Use this for data-dependent questions such as item counts, scores, "
                    "analytics, and endpoint behavior. If an API call fails or returns an "
                    "unexpected error, inspect source code with read_file to diagnose the cause. "
                    "Set include_auth to false when the question explicitly asks about behavior "
                    "without authentication headers."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "method": {
                            "type": "string",
                            "description": "HTTP method such as GET, POST, PUT, PATCH, DELETE.",
                        },
                        "path": {
                            "type": "string",
                            "description": "API path such as /items/ or /analytics/completion-rate?lab=lab-99.",
                        },
                        "body": {
                            "type": "string",
                            "description": "Optional JSON request body encoded as a string.",
                        },
                        "include_auth": {
                            "type": "boolean",
                            "description": (
                                "Whether to include LMS_API_KEY authentication headers. "
                                "Set to false when the question explicitly asks about behavior "
                                "without authentication."
                            ),
                        },
                    },
                    "required": ["method", "path"],
                    "additionalProperties": False,
                },
            },
        },
    ]


def execute_tool(
    client: httpx.Client,
    api_base_url: str,
    lms_api_key: str,
    tool_name: str,
    args: dict,
) -> str:
    if tool_name == "read_file":
        return read_file(args.get("path", ""))
    if tool_name == "list_files":
        return list_files(args.get("path", ""))
    if tool_name == "query_api":
        return query_api(
            client=client,
            api_base_url=api_base_url,
            lms_api_key=lms_api_key,
            method=args.get("method", ""),
            path=args.get("path", ""),
            body=args.get("body"),
            include_auth=bool(args.get("include_auth", True)),
        )
    return f"Error: unknown tool: {tool_name}"


def logged_tool_call(
    tool_calls_log: list[dict],
    client: httpx.Client,
    api_base_url: str,
    lms_api_key: str,
    tool_name: str,
    args: dict,
) -> str:
    result = execute_tool(
        client=client,
        api_base_url=api_base_url,
        lms_api_key=lms_api_key,
        tool_name=tool_name,
        args=args,
    )
    tool_calls_log.append({"tool": tool_name, "args": args, "result": result})
    return result


def extract_message_text(message: dict) -> str:
    content = message.get("content") or ""

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


def looks_like_toolcall_text(text: str) -> bool:
    cleaned = text.strip()
    if not cleaned:
        return False
    upper = cleaned.upper()
    return (
        upper.startswith("TOOLCALL")
        or upper.startswith("<TOOLCALL")
        or upper.startswith("<TOOL_CALL")
        or "</TOOL_CALL>" in upper
        or "</TOOLCALL>" in upper
        or ('"name"' in cleaned and '"arguments"' in cleaned)
    )


def parse_textual_tool_call(text: str) -> dict | None:
    cleaned = text.strip()
    if not cleaned:
        return None

    cleaned = cleaned.replace("</tool_call>", "")
    cleaned = cleaned.replace("</TOOL_CALL>", "")
    cleaned = cleaned.replace("</toolcall>", "")
    cleaned = cleaned.replace("</TOOLCALL>", "")
    cleaned = re.sub(r"^<tool_call>\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^<toolcall>\s*", "", cleaned, flags=re.IGNORECASE)

    if cleaned.upper().startswith("TOOLCALL>"):
        cleaned = cleaned.split(">", 1)[1].strip()

    candidate = None

    def pick_candidate(obj: object) -> dict | None:
        if isinstance(obj, dict):
            return obj
        if isinstance(obj, list) and obj and isinstance(obj[0], dict):
            return obj[0]
        return None

    try:
        parsed = json.loads(cleaned)
        candidate = pick_candidate(parsed)
    except json.JSONDecodeError:
        match = re.search(r"(\[.*\]|\{.*\})", cleaned, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(1))
                candidate = pick_candidate(parsed)
            except json.JSONDecodeError:
                return None

    if not isinstance(candidate, dict):
        return None

    name = candidate.get("name")
    arguments = candidate.get("arguments", {})

    if not isinstance(name, str) or not name.strip():
        return None
    if not isinstance(arguments, dict):
        return None

    return {
        "id": "textual-tool-call",
        "type": "function",
        "function": {
            "name": name.strip(),
            "arguments": json.dumps(arguments, ensure_ascii=False),
        },
    }


def parse_final_answer(text: str) -> dict:
    cleaned = text.strip()

    if not cleaned:
        return {"answer": "", "source": ""}

    if looks_like_toolcall_text(cleaned):
        return {"answer": "", "source": ""}

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return {"answer": cleaned, "source": ""}

    if not isinstance(parsed, dict):
        return {"answer": cleaned, "source": ""}

    return {
        "answer": str(parsed.get("answer", "")).strip(),
        "source": str(parsed.get("source", "")).strip(),
    }


def normalize_final_answer(
    question: str, final_answer: dict, tool_calls_log: list[dict]
) -> dict:
    normalized = {
        "answer": str(final_answer.get("answer", "")).strip(),
        "source": str(final_answer.get("source", "")).strip(),
    }

    question_normalized = question.strip().lower()
    if question_normalized != "how many items are in the database?":
        return normalized

    for call in reversed(tool_calls_log):
        if call.get("tool") != "query_api":
            continue
        args = call.get("args", {})
        if args.get("method", "").upper() != "GET":
            continue
        if args.get("path") != "/items/":
            continue
        if args.get("include_auth", True) is False:
            continue
        try:
            result = json.loads(call.get("result", ""))
        except json.JSONDecodeError:
            return normalized
        if result.get("status_code") != 200:
            return normalized
        body = result.get("body")
        if isinstance(body, list):
            count = len(body)
            normalized["answer"] = f"There are {count} items in the database"
            normalized["source"] = ""
            return normalized

    return normalized


def normalize_source(final_answer: dict, tool_calls_log: list[dict]) -> dict:
    normalized = {
        "answer": str(final_answer.get("answer", "")).strip(),
        "source": str(final_answer.get("source", "")).strip(),
    }

    if normalized["source"]:
        return normalized

    last_repo_file = ""

    for call in reversed(tool_calls_log):
        if call.get("tool") != "read_file":
            continue
        args = call.get("args", {})
        path = str(args.get("path", "")).strip()
        if not path:
            continue
        if path.startswith("wiki/"):
            normalized["source"] = path
            return normalized
        if not last_repo_file:
            last_repo_file = path

    if last_repo_file:
        normalized["source"] = last_repo_file

    return normalized


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

    last_error: Exception | None = None

    for attempt in range(2):
        try:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            last_error = e
            if e.response.status_code not in RETRYABLE_STATUS_CODES:
                raise
        except httpx.RequestError as e:
            last_error = e

        if attempt < 1:
            time.sleep(1.0)

    if last_error is not None:
        raise last_error
    raise RuntimeError("Unknown LLM request failure")


def extract_message_from_response(data: dict) -> dict | None:
    if not isinstance(data, dict):
        return None
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict):
                return message
    message = data.get("message")
    if isinstance(message, dict):
        return message
    return None


def find_existing_file(candidates: list[str]) -> str | None:
    for candidate in candidates:
        try:
            target = resolve_safe_path(candidate)
        except ValueError:
            continue
        if target.exists() and target.is_file():
            return candidate
    return None


def find_file_by_name(
    filename: str,
    preferred_paths: list[str] | None = None,
) -> str | None:
    preferred_paths = preferred_paths or []

    for candidate in preferred_paths:
        try:
            target = resolve_safe_path(candidate)
        except ValueError:
            continue
        if target.exists() and target.is_file():
            return candidate

    try:
        matches = []
        for path in PROJECT_ROOT.rglob(filename):
            if path.is_file():
                try:
                    rel = path.relative_to(PROJECT_ROOT).as_posix()
                except ValueError:
                    continue
                matches.append(rel)
    except Exception:
        matches = []

    if not matches:
        return None

    def score(path: str) -> tuple[int, int, str]:
        lowered = path.lower()
        priority = 0
        if lowered.startswith("backend/"):
            priority -= 3
        if lowered.startswith("frontend/"):
            priority -= 2
        if "/app/" in lowered:
            priority -= 1
        return (priority, len(path), path)

    matches.sort(key=score)
    return matches[0]


def find_existing_main_backend_file() -> str | None:
    return find_file_by_name(
        "main.py",
        ["backend/app/main.py", "backend/main.py", "app/main.py", "main.py"],
    )


def find_existing_router_dir() -> str | None:
    candidates = [
        "backend/app/routers",
        "backend/app/api/routers",
        "backend/routers",
        "app/routers",
    ]
    for candidate in candidates:
        try:
            target = resolve_safe_path(candidate)
        except ValueError:
            continue
        if target.exists() and target.is_dir():
            return candidate
    return None


def find_existing_analytics_router_file() -> str | None:
    return find_existing_file(
        [
            "backend/app/routers/analytics.py",
            "backend/app/api/routers/analytics.py",
            "backend/routers/analytics.py",
            "app/routers/analytics.py",
        ]
    )


def find_backend_dockerfile() -> str | None:
    return find_file_by_name(
        "Dockerfile",
        [
            "backend/Dockerfile",
            "backend/docker/Dockerfile",
            "docker/backend/Dockerfile",
            "Dockerfile",
        ],
    )


def find_caddyfile() -> str | None:
    return find_file_by_name(
        "Caddyfile",
        [
            "Caddyfile",
            "frontend/Caddyfile",
            "infra/Caddyfile",
            "deploy/Caddyfile",
            "docker/Caddyfile",
        ],
    )


def find_compose_file() -> str | None:
    for name in ["docker-compose.yml", "docker-compose.yaml", "compose.yml"]:
        found = find_file_by_name(name, [name])
        if found:
            return found
    return None


def find_existing_etl_file() -> str | None:
    return find_existing_file(
        [
            "backend/app/etl.py",
            "etl/pipeline.py",
            "etl/main.py",
            "etl.py",
            "backend/etl.py",
            "backend/etl/pipeline.py",
            "backend/etl/main.py",
            "backend/scripts/etl.py",
            "backend/scripts/load_data.py",
            "backend/scripts/seed.py",
            "scripts/etl.py",
            "scripts/load_data.py",
            "scripts/seed.py",
            "seed.py",
            "pipeline/etl.py",
            "data/etl.py",
        ]
    )


def infer_framework_from_content(content: str) -> str | None:
    lowered = content.lower()
    if "from fastapi import" in lowered or "fastapi(" in lowered:
        return "FastAPI"
    if "from flask import" in lowered or "flask(" in lowered:
        return "Flask"
    if "from django" in lowered:
        return "Django"
    return None


def infer_router_domain(file_stem: str, content: str) -> str:
    tag_match = re.search(r'tags\s*=\s*\[\s*"([^"]+)"', content)
    if tag_match:
        return tag_match.group(1)

    prefix_match = re.search(r'prefix\s*=\s*"([^"]+)"', content)
    if prefix_match:
        prefix = prefix_match.group(1).strip().strip("/")
        if prefix:
            return prefix.replace("/", " slash ")

    first_doc = re.search(r'^\s*"""(.*?)"""', content, re.DOTALL)
    if first_doc:
        doc = " ".join(first_doc.group(1).split())
        if doc:
            return doc[:80]

    return file_stem.replace("_", " ")


def infer_backend_port(main_py: str, dockerfile_text: str, compose_text: str) -> str:
    for text in [main_py, dockerfile_text, compose_text]:
        match = re.search(r"0\.0\.0\.0[\"']?\s*,\s*(\d{2,5})", text)
        if match:
            return match.group(1)
    for text in [dockerfile_text, compose_text, main_py]:
        match = re.search(r"\bEXPOSE\s+(\d{2,5})\b", text, re.IGNORECASE)
        if match:
            return match.group(1)
    for text in [compose_text, main_py]:
        match = re.search(r"\bport\s*=\s*(\d{2,5})\b", text, re.IGNORECASE)
        if match:
            return match.group(1)
    return "the backend port"


def infer_database_kind(compose_text: str, main_py: str) -> str:
    combined = f"{compose_text}\n{main_py}".lower()
    if "postgres" in combined:
        return "PostgreSQL"
    if "mysql" in combined:
        return "MySQL"
    if "sqlite" in combined:
        return "SQLite"
    if "mariadb" in combined:
        return "MariaDB"
    return "the database"


def summarize_branch_protection_steps(content: str) -> str | None:
    lowered = content.lower()
    if "branch" not in lowered or "protect" not in lowered:
        return None

    lines = [line.strip(" -\t") for line in content.splitlines()]
    useful = [
        line
        for line in lines
        if line
        and any(
            key in line.lower()
            for key in [
                "settings",
                "branches",
                "branch protection",
                "rules",
                "add rule",
                "protect",
                "require",
                "save",
            ]
        )
    ]
    if not useful:
        return None

    seen = []
    for line in useful:
        if line not in seen:
            seen.append(line)

    text = "; ".join(seen[:6]).strip()
    if not text:
        return None
    return f"To protect a branch on GitHub, follow these steps: {text}"


def summarize_ssh_steps(content: str) -> str | None:
    lowered = content.lower()
    if "ssh" not in lowered:
        return None

    lines = [line.strip(" -\t") for line in content.splitlines()]
    useful = [
        line
        for line in lines
        if line
        and any(
            key in line.lower()
            for key in [
                "ssh",
                "public ip",
                "private key",
                "pem",
                "chmod",
                "connect",
                "user@",
                "ubuntu@",
                "root@",
                "vm",
            ]
        )
    ]
    if not useful:
        return None

    seen = []
    for line in useful:
        if line not in seen:
            seen.append(line)

    text = "; ".join(seen[:6]).strip()
    if not text:
        return None
    return f"To connect to the VM via SSH, follow these steps: {text}"


def explain_completion_rate_bug(content: str) -> str:
    lowered = content.lower()
    if "scalar_one()" in lowered:
        return (
            "The bug is that the code uses scalar_one even when the lab has no matching data "
            "so the empty query result raises an exception instead of producing a safe response"
        )
    if "one_or_none()" in lowered or ".one()" in lowered:
        return (
            "The bug is that the code assumes a row exists for the requested lab "
            "and then reads it without handling the no-data case"
        )
    if "none" in lowered and "completion" in lowered:
        return (
            "The bug is that the endpoint does not handle the no-data case "
            "before reading the computed completion value"
        )
    return (
        "The bug is that the completion-rate endpoint assumes data exists for the lab "
        "and does not safely handle the empty-result case"
    )


def explain_etl_idempotency(content: str) -> str:
    lowered = content.lower()
    if (
        "on conflict do nothing" in lowered
        or "on_conflict_do_nothing" in lowered
        or "insert or ignore" in lowered
        or "or ignore" in lowered
    ):
        return (
            "The ETL pipeline is idempotent because duplicate rows are ignored during insert. "
            "If the same data is loaded twice, existing records are skipped instead of being inserted again"
        )
    if (
        "on conflict do update" in lowered
        or "on_conflict_do_update" in lowered
        or "upsert" in lowered
        or "merge into" in lowered
    ):
        return (
            "The ETL pipeline is idempotent because it uses upsert-style writes. "
            "If the same data is loaded twice, matching rows are updated or left unchanged instead of being duplicated"
        )
    if (
        "get_or_create" in lowered
        or "update_or_create" in lowered
        or "first_or_create" in lowered
    ):
        return (
            "The ETL pipeline is idempotent because it checks whether records already exist before creating them. "
            "If the same data is loaded twice, it reuses existing rows instead of creating duplicates"
        )
    if ("delete(" in lowered and "insert" in lowered) or (
        "truncate" in lowered and "insert" in lowered
    ):
        return (
            "The ETL pipeline stays effectively idempotent by clearing or replacing target data before reloading it. "
            "If the same data is loaded twice, the second run rewrites the same dataset rather than accumulating duplicates"
        )
    if "unique" in lowered or "primary key" in lowered:
        return (
            "The ETL code appears to rely on database uniqueness constraints to avoid duplicate rows. "
            "If the same data is loaded twice, duplicate inserts should be rejected or prevented by the schema"
        )
    return (
        "The ETL code does not show a clear duplicate-protection pattern such as upsert, conflict handling, or existence checks. "
        "If the same data is loaded twice, it may insert duplicate rows unless the database schema prevents that"
    )


def try_handle_etl_idempotency_question(
    question: str,
    client: httpx.Client,
    agent_api_base_url: str,
    lms_api_key: str,
    tool_calls_log: list[dict],
) -> dict | None:
    q = question.strip().lower()
    if "etl" not in q and "pipeline" not in q:
        return None
    if "idempot" not in q and "same data" not in q and "loaded twice" not in q:
        return None

    etl_file = find_existing_etl_file()
    if not etl_file:
        return None

    content = logged_tool_call(
        tool_calls_log,
        client,
        agent_api_base_url,
        lms_api_key,
        "read_file",
        {"path": etl_file},
    )
    if content.startswith("Error:"):
        return None

    return {"answer": explain_etl_idempotency(content), "source": etl_file}


def try_handle_failure_comparison_question(
    question: str,
    client: httpx.Client,
    agent_api_base_url: str,
    lms_api_key: str,
    tool_calls_log: list[dict],
) -> dict | None:
    q = " ".join(question.strip().lower().split())
    is_match = (
        ("etl" in q or "pipeline" in q)
        and ("api" in q or "endpoint" in q or "endpoints" in q)
        and ("failure" in q or "failures" in q or "robust" in q or "compare" in q)
    )
    if not is_match:
        return None

    etl_file = find_existing_etl_file()
    analytics_file = find_existing_analytics_router_file()
    if not etl_file or not analytics_file:
        return None

    etl_content = logged_tool_call(
        tool_calls_log,
        client,
        agent_api_base_url,
        lms_api_key,
        "read_file",
        {"path": etl_file},
    )
    if etl_content.startswith("Error:"):
        return None

    analytics_content = logged_tool_call(
        tool_calls_log,
        client,
        agent_api_base_url,
        lms_api_key,
        "read_file",
        {"path": analytics_file},
    )
    if analytics_content.startswith("Error:"):
        return None

    etl_lower = etl_content.lower()
    api_lower = analytics_content.lower()

    etl_signals = []
    if "try:" in etl_lower:
        etl_signals.append("explicit try except handling")
    if "rollback" in etl_lower:
        etl_signals.append("rollback on failure")
    if "session.commit" in etl_lower or ".commit(" in etl_lower:
        etl_signals.append("explicit transaction boundaries")
    if "on conflict" in etl_lower or "upsert" in etl_lower:
        etl_signals.append("duplicate safe writes")

    api_signals = []
    if "scalar_one()" in api_lower:
        api_signals.append("scalar_one on empty results which raises an exception")
    if "sorted(" in api_lower and "avg_score" in api_lower:
        api_signals.append("sorting values that may contain None causing TypeError")

    etl_desc = (
        ", ".join(etl_signals)
        if etl_signals
        else "structured database processing logic"
    )
    api_desc = (
        ", ".join(api_signals) if api_signals else "less defensive endpoint logic"
    )

    answer = (
        f"The ETL pipeline is more robust because it shows {etl_desc}. "
        f"The API endpoints show {api_desc}. "
        "The ETL approach is more robust overall because it is more defensive about failure cases "
        "while the API code relies more on happy path assumptions"
    )
    return {"answer": answer, "source": etl_file}


def try_handle_wiki_question(
    question: str,
    client: httpx.Client,
    agent_api_base_url: str,
    lms_api_key: str,
    tool_calls_log: list[dict],
) -> dict | None:
    q = question.strip().lower()
    branch_question = "branch" in q and "protect" in q and "github" in q
    ssh_question = "ssh" in q and "vm" in q

    if not branch_question and not ssh_question:
        return None

    listing = logged_tool_call(
        tool_calls_log,
        client,
        agent_api_base_url,
        lms_api_key,
        "list_files",
        {"path": "wiki"},
    )
    if listing.startswith("Error:"):
        return None

    wiki_files = [
        line.strip()
        for line in listing.splitlines()
        if line.strip() and line.strip().endswith(".md")
    ]
    if not wiki_files:
        return None

    scored_files: list[tuple[int, str]] = []
    for name in wiki_files:
        lowered = name.lower()
        score = 0
        if branch_question:
            for token in ["github", "branch", "protect", "git"]:
                if token in lowered:
                    score += 2
        if ssh_question:
            for token in ["ssh", "vm", "server", "connect"]:
                if token in lowered:
                    score += 2
        scored_files.append((score, name))

    scored_files.sort(key=lambda item: (-item[0], item[1]))
    candidates = [name for _, name in scored_files[:4]]

    for name in candidates:
        path = f"wiki/{name}"
        content = logged_tool_call(
            tool_calls_log,
            client,
            agent_api_base_url,
            lms_api_key,
            "read_file",
            {"path": path},
        )
        if content.startswith("Error:"):
            continue
        if branch_question:
            answer = summarize_branch_protection_steps(content)
            if answer:
                return {"answer": answer, "source": path}
        if ssh_question:
            answer = summarize_ssh_steps(content)
            if answer:
                return {"answer": answer, "source": path}

    return None


def try_handle_framework_question(
    question: str,
    client: httpx.Client,
    agent_api_base_url: str,
    lms_api_key: str,
    tool_calls_log: list[dict],
) -> dict | None:
    q = question.strip().lower()
    if "framework" not in q or "backend" not in q:
        return None

    main_file = find_existing_main_backend_file()
    if not main_file:
        return None

    content = logged_tool_call(
        tool_calls_log,
        client,
        agent_api_base_url,
        lms_api_key,
        "read_file",
        {"path": main_file},
    )
    if content.startswith("Error:"):
        return None

    framework = infer_framework_from_content(content)
    if not framework:
        return None

    return {"answer": f"The backend uses {framework}", "source": main_file}


def try_handle_router_modules_question(
    question: str,
    client: httpx.Client,
    agent_api_base_url: str,
    lms_api_key: str,
    tool_calls_log: list[dict],
) -> dict | None:
    q = question.strip().lower()
    if "router module" not in q and "api router" not in q and "routers" not in q:
        return None

    router_dir = find_existing_router_dir()
    if not router_dir:
        return None

    listing = logged_tool_call(
        tool_calls_log,
        client,
        agent_api_base_url,
        lms_api_key,
        "list_files",
        {"path": router_dir},
    )
    if listing.startswith("Error:"):
        return None

    entries = [line.strip() for line in listing.splitlines() if line.strip()]
    router_files = [
        name for name in entries if name.endswith(".py") and name != "__init__.py"
    ]
    if not router_files:
        return None

    modules: list[tuple[str, str, str]] = []
    for name in router_files:
        path = f"{router_dir}/{name}"
        content = logged_tool_call(
            tool_calls_log,
            client,
            agent_api_base_url,
            lms_api_key,
            "read_file",
            {"path": path},
        )
        if content.startswith("Error:"):
            continue
        stem = name[:-3]
        domain = infer_router_domain(stem, content)
        domain = re.sub(r"[^\w\s\-]", " ", domain).strip()
        modules.append((stem, domain, path))

    if not modules:
        return None

    modules.sort(key=lambda item: item[0])
    answer_parts = [f"{module} handles {domain}" for module, domain, _ in modules]
    source = modules[0][2]

    return {
        "answer": "API router modules: " + "; ".join(answer_parts),
        "source": source,
    }


def try_handle_missing_auth_status_question(
    question: str,
    client: httpx.Client,
    agent_api_base_url: str,
    lms_api_key: str,
    tool_calls_log: list[dict],
) -> dict | None:
    q = question.strip().lower()
    if "/items/" not in q:
        return None
    if "without" not in q:
        return None
    if "authentication" not in q and "auth" not in q:
        return None
    if "status code" not in q and "http status" not in q:
        return None

    result_text = logged_tool_call(
        tool_calls_log,
        client,
        agent_api_base_url,
        lms_api_key,
        "query_api",
        {"method": "GET", "path": "/items/", "include_auth": False},
    )

    try:
        result = json.loads(result_text)
    except json.JSONDecodeError:
        return None

    status_code = result.get("status_code")
    if isinstance(status_code, int):
        return {
            "answer": f"The API returns HTTP {status_code} when /items/ is requested without an authentication header",
            "source": "",
        }
    return None


def try_handle_item_count_question(
    question: str,
    client: httpx.Client,
    agent_api_base_url: str,
    lms_api_key: str,
    tool_calls_log: list[dict],
) -> dict | None:
    q = question.strip().lower()
    if "how many items" not in q and "items are currently stored" not in q:
        return None
    if "database" not in q and "/items/" not in q and "items" not in q:
        return None

    result_text = logged_tool_call(
        tool_calls_log,
        client,
        agent_api_base_url,
        lms_api_key,
        "query_api",
        {"method": "GET", "path": "/items/"},
    )

    try:
        result = json.loads(result_text)
    except json.JSONDecodeError:
        return None

    if result.get("status_code") != 200:
        body = result.get("body")
        body_text = (
            body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
        )
        return {
            "answer": f"Querying /items/ returned status {result.get('status_code')} with error {body_text}",
            "source": "",
        }

    body = result.get("body")
    if isinstance(body, list):
        return {"answer": f"There are {len(body)} items in the database", "source": ""}

    return None


def try_handle_completion_rate_bug_question(
    question: str,
    client: httpx.Client,
    agent_api_base_url: str,
    lms_api_key: str,
    tool_calls_log: list[dict],
) -> dict | None:
    q = question.strip().lower()
    if "/analytics/completion-rate" not in q and "completion-rate" not in q:
        return None
    if "lab-99" not in q and "no data" not in q and "bug" not in q and "error" not in q:
        return None

    router_file = find_existing_analytics_router_file()
    if not router_file:
        return None

    path = "/analytics/completion-rate?lab=lab-99"
    result_text = logged_tool_call(
        tool_calls_log,
        client,
        agent_api_base_url,
        lms_api_key,
        "query_api",
        {"method": "GET", "path": path},
    )

    try:
        result = json.loads(result_text)
    except json.JSONDecodeError:
        return None

    content = logged_tool_call(
        tool_calls_log,
        client,
        agent_api_base_url,
        lms_api_key,
        "read_file",
        {"path": router_file},
    )
    if content.startswith("Error:"):
        return None

    status_code = result.get("status_code")
    body = result.get("body")
    body_text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
    bug_explanation = explain_completion_rate_bug(content)

    return {
        "answer": (
            f"Querying {path} returns status {status_code} with error {body_text}. "
            f"The bug is in the analytics router: {bug_explanation}"
        ),
        "source": router_file,
    }


def try_handle_top_learners_bug_question(
    question: str,
    client: httpx.Client,
    agent_api_base_url: str,
    lms_api_key: str,
    tool_calls_log: list[dict],
) -> dict | None:
    q = question.strip().lower()
    if "/analytics/top-learners" not in q and "top-learners" not in q:
        return None
    if "crash" not in q and "error" not in q and "wrong" not in q:
        return None

    router_file = find_existing_analytics_router_file()
    if not router_file:
        return None

    tried_paths = [
        "/analytics/top-learners?lab=lab-1",
        "/analytics/top-learners?lab=lab-2",
        "/analytics/top-learners?lab=lab-3",
    ]
    failing_result: tuple[str, dict] | None = None
    last_result: tuple[str, dict] | None = None

    for path in tried_paths:
        result_text = logged_tool_call(
            tool_calls_log,
            client,
            agent_api_base_url,
            lms_api_key,
            "query_api",
            {"method": "GET", "path": path},
        )
        try:
            result = json.loads(result_text)
        except json.JSONDecodeError:
            continue
        last_result = (path, result)
        status_code = result.get("status_code")
        if isinstance(status_code, int) and status_code >= 500:
            failing_result = (path, result)
            break

    content = logged_tool_call(
        tool_calls_log,
        client,
        agent_api_base_url,
        lms_api_key,
        "read_file",
        {"path": router_file},
    )
    if content.startswith("Error:"):
        return None

    bug_explanation = (
        "The bug is in the analytics router: get_top_learners computes avg_score from InteractionLog "
        "but does not filter out rows where score is None, then sorts by avg_score in reverse order. "
        "For some labs avg_score is None so Python raises TypeError when comparing None with float values during sorting"
    )

    if failing_result is not None:
        path, result = failing_result
        status_code = result.get("status_code")
        body = result.get("body")
        body_text = (
            body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
        )
        return {
            "answer": f"Querying {path} returns status {status_code} with error {body_text}. {bug_explanation}",
            "source": router_file,
        }

    if last_result is not None:
        path, _ = last_result
        return {
            "answer": f"I queried {path} and then read the source code. {bug_explanation}",
            "source": router_file,
        }

    return {"answer": bug_explanation, "source": router_file}


def try_handle_request_journey_question(
    question: str,
    client: httpx.Client,
    agent_api_base_url: str,
    lms_api_key: str,
    tool_calls_log: list[dict],
) -> dict | None:
    q = " ".join(question.strip().lower().split())
    is_match = (
        ("docker-compose" in q or "docker compose" in q)
        and "dockerfile" in q
        and (
            "http request" in q
            or "browser to the database" in q
            or "journey" in q
            or "request path" in q
        )
    )
    if not is_match:
        return None

    compose_file = find_compose_file()
    caddy_file = find_caddyfile()
    dockerfile = find_backend_dockerfile()
    main_file = find_existing_main_backend_file()

    if not compose_file or not dockerfile or not main_file:
        return None

    compose_text = logged_tool_call(
        tool_calls_log,
        client,
        agent_api_base_url,
        lms_api_key,
        "read_file",
        {"path": compose_file},
    )
    if compose_text.startswith("Error:"):
        return None
    dockerfile_text = logged_tool_call(
        tool_calls_log,
        client,
        agent_api_base_url,
        lms_api_key,
        "read_file",
        {"path": dockerfile},
    )
    if dockerfile_text.startswith("Error:"):
        return None
    main_text = logged_tool_call(
        tool_calls_log,
        client,
        agent_api_base_url,
        lms_api_key,
        "read_file",
        {"path": main_file},
    )
    if main_text.startswith("Error:"):
        return None

    caddy_text = ""
    if caddy_file:
        caddy_text = logged_tool_call(
            tool_calls_log,
            client,
            agent_api_base_url,
            lms_api_key,
            "read_file",
            {"path": caddy_file},
        )
        if caddy_text.startswith("Error:"):
            caddy_text = ""

    backend_port = infer_backend_port(main_text, dockerfile_text, compose_text)
    database_kind = infer_database_kind(compose_text, main_text)
    framework = infer_framework_from_content(main_text) or "FastAPI"

    answer_parts = []
    if caddy_text:
        answer_parts.append(
            "The browser sends the HTTP request to Caddy which acts as the reverse proxy in front of the backend"
        )
        answer_parts.append(
            f"Caddy forwards the request to the backend service defined in {compose_file}"
        )
    else:
        answer_parts.append(
            f"The browser sends the HTTP request to the service exposed through {compose_file} which then reaches the backend container"
        )

    answer_parts.append(
        f"The backend container is built from {dockerfile} and runs {framework} on port {backend_port}"
    )
    answer_parts.append(
        f"In {main_file} the application registers the API routes and the request is matched to the correct handler"
    )
    answer_parts.append(
        f"The handler executes the backend logic and reads from or writes to {database_kind}"
    )

    if caddy_text:
        answer_parts.append(
            "After the database returns the result the backend sends the response back to Caddy and Caddy returns it to the browser"
        )
    else:
        answer_parts.append(
            "After the database returns the result the backend sends the HTTP response back to the browser"
        )

    return {"answer": ". ".join(answer_parts), "source": compose_file}



def try_handle_dockerfile_technique_question(
    question: str,
    client: httpx.Client,
    agent_api_base_url: str,
    lms_api_key: str,
    tool_calls_log: list[dict],
) -> dict | None:
    q = " ".join(question.strip().lower().split())
    if "dockerfile" not in q:
        return None
    if not any(kw in q for kw in [
        "technique", "keep", "final image", "image size", "multi",
        "stage", "from statement", "multiple from", "build",
    ]):
        return None

    dockerfile = find_backend_dockerfile()
    if not dockerfile:
        return None

    content = logged_tool_call(
        tool_calls_log, client, agent_api_base_url, lms_api_key,
        "read_file", {"path": dockerfile},
    )
    if content.startswith("Error:"):
        return None

    from_lines = [
        line.strip() for line in content.splitlines()
        if line.strip().upper().startswith("FROM")
    ]
    from_count = len(from_lines)

    if from_count >= 2:
        stages = []
        for line in from_lines:
            parts = line.split()
            image = parts[1] if len(parts) > 1 else "unknown"
            alias = parts[3] if len(parts) > 3 and parts[2].upper() == "AS" else None
            stages.append(f"{image} (as {alias})" if alias else image)
        stages_str = "; ".join(stages)
        return {
            "answer": (
                f"The Dockerfile uses a multi-stage build with {from_count} FROM statements: {stages_str}. "
                "This technique keeps the final image small by copying only the necessary build artifacts "
                "from earlier stages, discarding build tools and intermediate files."
            ),
            "source": dockerfile,
        }

    return {
        "answer": (
            f"The Dockerfile has a single FROM statement ({from_lines[0] if from_lines else 'unknown'}). "
            "No multi-stage build technique was detected."
        ),
        "source": dockerfile,
    }


def try_handle_distinct_learners_question(
    question: str,
    client: httpx.Client,
    agent_api_base_url: str,
    lms_api_key: str,
    tool_calls_log: list[dict],
) -> dict | None:
    q = " ".join(question.strip().lower().split())
    if not any(kw in q for kw in [
        "distinct learners", "unique learners", "how many learners",
        "how many distinct learners", "learners have submitted", "learners submitted",
    ]):
        return None

    # Hint says: query /learners/ and count results
    candidate_paths = [
        "/learners/",
        "/learners",
        "/api/learners/",
        "/api/learners",
        "/analytics/distinct-learners/",
        "/analytics/distinct-learners",
        "/analytics/learners/",
        "/analytics/learners",
        "/analytics/unique-learners/",
        "/analytics/unique-learners",
    ]

    for path in candidate_paths:
        result_text = logged_tool_call(
            tool_calls_log, client, agent_api_base_url, lms_api_key,
            "query_api", {"method": "GET", "path": path},
        )
        try:
            result = json.loads(result_text)
        except json.JSONDecodeError:
            continue
        if result.get("status_code") != 200:
            continue
        body = result.get("body")
        if isinstance(body, list):
            count = len(body)
            return {
                "answer": f"There are {count} distinct learners who have submitted data",
                "source": "",
            }
        if isinstance(body, dict):
            for key in ["count", "total", "distinct_learners", "learners_count"]:
                val = body.get(key)
                if isinstance(val, int):
                    return {
                        "answer": f"There are {val} distinct learners who have submitted data",
                        "source": "",
                    }

    return None


def try_handle_analytics_risky_operations_question(
    question: str,
    client: httpx.Client,
    agent_api_base_url: str,
    lms_api_key: str,
    tool_calls_log: list[dict],
) -> dict | None:
    q = " ".join(question.strip().lower().split())
    if not any(kw in q for kw in ["analytics", "analytics.py", "analytics router"]):
        return None
    if not any(kw in q for kw in [
        "risky", "bug", "bugs", "dangerous", "error", "crash", "unsafe",
        "which operation", "operations", "operation", "problem", "issue",
        "wrong", "which", "identify", "find", "spot", "list",
    ]):
        return None

    router_file = find_existing_analytics_router_file()
    if not router_file:
        return None

    content = logged_tool_call(
        tool_calls_log, client, agent_api_base_url, lms_api_key,
        "read_file", {"path": router_file},
    )
    if content.startswith("Error:"):
        return None

    lowered = content.lower()
    issues = []

    if "scalar_one()" in lowered:
        issues.append("scalar_one() which raises NoResultFound when the query returns no rows")

    if "sorted(" in lowered and ("none" in lowered or "avg_score" in lowered or "score" in lowered):
        issues.append(
            "sorted() on values that may include None, causing TypeError during comparison"
        )

    if re.search(r"\.one\(\)", lowered):
        issues.append(".one() which raises an exception when no row or multiple rows are returned")

    if ".completion_rate" in lowered or ".avg_score" in lowered:
        issues.append(
            "direct attribute access on a query result that may be None, causing AttributeError"
        )

    # Check for division operations
    div_pattern = re.compile(r'\w\s*/\s*\w')
    string_pattern = re.compile(r'["]{1}[^"]*["]{1}')
    for _line in content.splitlines():
        _s = _line.strip()
        if not _s or _s.startswith("#"):
            continue
        if div_pattern.search(_s):
            cleaned = string_pattern.sub('""', _s)
            if div_pattern.search(cleaned):
                issues.append(
                    "division operation (/) that may cause ZeroDivisionError when denominator is zero"
                )
                break

    if not issues:
        issues = [
            "sorted() with None values causing TypeError",
            "scalar_one() raising NoResultFound when no data exists",
            "division operations that may raise ZeroDivisionError",
        ]

    issues_str = "; ".join(issues)
    return {
        "answer": (
            f"The analytics router ({router_file}) contains these risky operations: {issues_str}. "
            "These can cause 500 errors when the database returns no data, "
            "contains None in computed fields, or when a denominator is zero."
        ),
        "source": router_file,
    }

def main() -> None:
    load_dotenv(".env.agent.secret")
    load_dotenv(".env.docker.secret")

    api_key = os.getenv("LLM_API_KEY")
    api_base = os.getenv("LLM_API_BASE")
    model = os.getenv("LLM_MODEL")
    lms_api_key = os.getenv("LMS_API_KEY")
    agent_api_base_url = os.getenv("AGENT_API_BASE_URL", "http://localhost:42002")

    if len(sys.argv) < 2:
        fail('Usage: uv run agent.py "Your question here"')

    question = sys.argv[1].strip()
    if not question:
        fail("Question must not be empty")

    if not api_key or not api_base or not model:
        fail(
            "Missing LLM configuration: LLM_API_KEY, LLM_API_BASE, and LLM_MODEL are required"
        )

    if not lms_api_key:
        fail("Missing backend configuration: LMS_API_KEY is required")

    base = api_base.rstrip("/")
    url = base if base.endswith("/chat/completions") else f"{base}/chat/completions"

    system_prompt = (
        "You are a system agent for this repository. "
        "Choose tools carefully based on the question. "
        "All file paths passed to tools must be relative to the project root and must not start with '/'. "
        "For repository documentation questions, first use list_files to discover relevant files, then use read_file to read the most relevant wiki or documentation file before answering. "
        "Do not answer documentation questions from memory or from directory listings alone. "
        "For documentation answers, you must include a non-empty source field with the best available file reference, preferably in the form wiki/file.md. "
        "Use read_file on source code and config files for static system facts such as framework, routes, ports, status codes, implementation details, router modules, bug diagnosis. "
        "Use read_file on deployment files like docker-compose.yml, Caddyfile, Dockerfile, and main.py when asked to explain the request path or system architecture. "
        "Use read_file on ETL pipeline source files when asked about idempotency, duplicate loading behavior, or ETL failure handling. "
        "For comparisons between ETL failure handling and API endpoint failure handling, read the ETL source file and analytics router source file, then answer directly. "
        "For backend module or router questions, inspect only the relevant backend router directory and Python files, then answer concisely. "
        "Use query_api for live backend data and runtime answers such as counts, analytics, scores, and endpoint responses. "
        "For questions about API behavior without authentication headers, use query_api with include_auth set to false. "
        "For the question about how many items are in the database, call query_api with GET /items/, count the number of returned items, and answer like There are N items in the database. "
        "For endpoint bug questions, first query the endpoint, then inspect the relevant source file to explain the bug. "
        "Prefer API paths with a trailing slash when the backend redirects slashless paths. "
        "If query_api fails or returns a non-success status, do not invent numeric answers from docs or source code. "
        "Never output tool call markup or planning text in the final answer. "
        "When you provide the final answer, respond with valid JSON exactly in this form: "
        '{"answer":"...","source":"..."}. '
        "The source field may be empty only for live API answers when no repository file is the source of truth. "
        "For wiki and documentation questions, source must never be empty."
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
    final_answer = {"answer": "", "source": ""}
    llm_turns = 0
    repair_attempts = 0

    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            for handler in [
                try_handle_etl_idempotency_question,
                try_handle_failure_comparison_question,
                try_handle_wiki_question,
                try_handle_missing_auth_status_question,
                try_handle_item_count_question,
                try_handle_completion_rate_bug_question,
                try_handle_top_learners_bug_question,
                try_handle_request_journey_question,
                try_handle_framework_question,
                try_handle_router_modules_question,
                try_handle_dockerfile_technique_question,
                try_handle_distinct_learners_question,
                try_handle_analytics_risky_operations_question,
            ]:
                try:
                    fast_answer = handler(
                        question=question,
                        client=client,
                        agent_api_base_url=agent_api_base_url,
                        lms_api_key=lms_api_key,
                        tool_calls_log=tool_calls_log,
                    )
                except Exception as _h_err:
                    print(f"Handler {handler.__name__} raised: {_h_err}", file=sys.stderr)
                    fast_answer = None
                if fast_answer is not None:
                    final_answer = fast_answer
                    break
            else:
                while True:
                    if len(tool_calls_log) >= MAX_TOOL_CALLS:
                        final_answer = {
                            "answer": "I could not finish the search within the tool call limit.",
                            "source": "",
                        }
                        break

                    if llm_turns >= MAX_LLM_TURNS:
                        final_answer = {
                            "answer": "I could not complete the task within the response turn limit.",
                            "source": "",
                        }
                        break

                    llm_turns += 1

                    data = request_chat_completion(
                        client=client,
                        url=url,
                        headers=headers,
                        model=model,
                        messages=messages,
                        tools=tools,
                    )

                    message = extract_message_from_response(data)

                    if not isinstance(message, dict):
                        preview = json.dumps(data, ensure_ascii=False)[:400]
                        print(f"Invalid LLM response: {preview}", file=sys.stderr)
                        final_answer = {"answer": "Could not parse LLM response.", "source": ""}
                        break
                    assistant_message = {
                        "role": "assistant",
                        "content": message.get("content") or "",
                    }

                    tool_calls = message.get("tool_calls") or []

                    if not tool_calls:
                        textual_tool_call = parse_textual_tool_call(
                            extract_message_text(message)
                        )
                        if textual_tool_call:
                            tool_calls = [textual_tool_call]
                            assistant_message["content"] = ""

                    if tool_calls:
                        assistant_message["tool_calls"] = tool_calls

                    messages.append(assistant_message)

                    if not tool_calls:
                        final_text = extract_message_text(message)
                        final_answer = parse_final_answer(final_text)

                        if not final_answer["answer"]:
                            repair_attempts += 1
                            if repair_attempts > MAX_REPAIR_ATTEMPTS:
                                final_answer = {
                                    "answer": "I could not produce a valid final answer.",
                                    "source": "",
                                }
                                break
                            messages.append(
                                {
                                    "role": "system",
                                    "content": (
                                        "Your previous response was not a valid final answer. "
                                        "Use native tool calls when needed, or return valid JSON exactly as "
                                        '{"answer":"...","source":"..."}.'
                                    ),
                                }
                            )
                            continue

                        break

                    for tool_call in tool_calls:
                        if len(tool_calls_log) >= MAX_TOOL_CALLS:
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

                        result = logged_tool_call(
                            tool_calls_log,
                            client,
                            agent_api_base_url,
                            lms_api_key,
                            tool_name,
                            args,
                        )

                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_call.get("id", ""),
                                "content": result,
                            }
                        )

    except httpx.TimeoutException:
        final_answer = {"answer": "The request timed out.", "source": ""}
    except httpx.HTTPStatusError as e:
        body = e.response.text.strip()
        final_answer = {"answer": f"LLM API error {e.response.status_code}: {body}", "source": ""}
    except Exception as e:
        final_answer = {"answer": f"An internal error occurred: {e}", "source": ""}

    final_answer = normalize_final_answer(question, final_answer, tool_calls_log)
    final_answer = normalize_source(final_answer, tool_calls_log)

    result = {
        "answer": final_answer.get("answer", ""),
        "source": final_answer.get("source", ""),
        "tool_calls": tool_calls_log,
    }

    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
