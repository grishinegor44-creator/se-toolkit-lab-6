# AGENT.md

## Overview

This project implements a CLI-based documentation agent in `agent.py`.

The agent answers user questions about the repository documentation by calling an OpenAI-compatible chat completions API and, when needed, using tools to inspect files inside the project. Unlike the basic Task 1 version, this agent can explore the repository and return structured JSON with the answer, source reference, and tool call history.

## Output Format

The CLI returns a JSON object with the following fields:

- `answer` — the final answer to the user question
- `source` — the documentation source used for the answer, ideally in `file-path#section-anchor` format
- `tool_calls` — a list of all executed tool calls, including tool name, arguments, and result

Example shape:

```json
{
  "answer": "Edit the conflicting file, choose which changes to keep, then stage and commit.",
  "source": "wiki/git-workflow.md#resolving-merge-conflicts",
  "tool_calls": [
    {
      "tool": "list_files",
      "args": { "path": "wiki" },
      "result": "git-workflow.md\n..."
    },
    {
      "tool": "read_file",
      "args": { "path": "wiki/git-workflow.md" },
      "result": "..."
    }
  ]
}
