# AGENT.md

## Overview

This project implements a CLI-based system agent in `agent.py`.

The agent answers several classes of questions about the repository and the running backend system. It can still inspect repository files, but unlike the earlier documentation-only version, it can now also query the deployed backend API for live data. This makes it suitable for both static system questions and runtime questions.

The agent communicates with an OpenAI-compatible chat completions API, supports tool calling, and returns a structured JSON result with the final answer, optional source, and full tool call history.

## Available tools

The agent exposes three tools to the LLM:

- `list_files(path)` — lists files and directories relative to the project root. This is mainly used to discover where documentation, source code, or configuration files are located.
- `read_file(path)` — reads a file relative to the project root. This is used for wiki lookup, source code inspection, framework detection, route discovery, port lookup, status-code analysis, and bug diagnosis.
- `query_api(method, path, body?)` — sends an authenticated HTTP request to the backend API and returns a JSON string with:
  - `status_code`
  - `body`

## Authentication and configuration

The agent reads all configuration from environment variables.

LLM-related variables:

- `LLM_API_KEY`
- `LLM_API_BASE`
- `LLM_MODEL`

Backend-related variables:

- `LMS_API_KEY`
- `AGENT_API_BASE_URL`

`LMS_API_KEY` is used only for backend authentication in `query_api`. It must not be confused with `LLM_API_KEY`, which is used only for the LLM provider.

`AGENT_API_BASE_URL` defines the base URL for backend requests. If it is not set, the agent uses the default value `http://localhost:42002`.

This design is important because the autochecker injects its own credentials and backend URL. Hardcoded keys, URLs, or model names would make the agent fail in the grading environment.

## Tool selection strategy

The system prompt teaches the model to choose tools based on the question type.

- For documentation questions, the agent should inspect wiki or repository documentation files.
- For static system facts such as framework, ports, routes, implementation details, and status codes, the agent should inspect source code with `read_file`.
- For live data questions such as item count, analytics, scores, or endpoint responses, the agent should use `query_api`.
- For debugging questions, the agent may combine tools: first query the backend, then inspect source files to explain an error.

This separation is important because documentation may be outdated, while the source code and running backend are the real sources of truth.

## Output format

The CLI returns a JSON object with the following fields:

- `answer` — the final answer to the user question
- `source` — an optional source reference; for documentation or code questions it should contain the most relevant file path or section
- `tool_calls` — a list of executed tool calls with tool name, arguments, and result

Example shape:

```json
{
  "answer": "There are 120 items in the database.",
  "source": "",
  "tool_calls": [
    {
      "tool": "query_api",
      "args": {
        "method": "GET",
        "path": "/items/"
      },
      "result": "{\"status_code\":200,\"body\":[...]}"
    }
  ]
}
