# Task 2 Plan

## Goal

In Task 2, I will extend the CLI from Task 1 into a documentation agent that can inspect the project wiki using tools. The agent must answer questions by exploring repository files, then return a JSON object with `answer`, `source`, and `tool_calls`.

## High-Level Approach

The agent will use two tools:

- `list_files` to discover relevant files in the wiki or repository
- `read_file` to inspect file contents and extract the answer

The main workflow will be an agentic loop: the model receives the user question and available tool definitions, decides whether to call a tool, gets the tool result, and either continues reasoning or produces the final answer.

## Tool Schemas

I will define the following function-calling schemas in `agent.py`.

### `read_file`

**Purpose:** read a file from the repository using a path relative to the project root.

**Parameters:**
- `path` (`string`) — relative file path from the project root

**Returns:**
- file contents as a string
- an error message if the file does not exist, is unreadable, or is outside the allowed directory

### `list_files`

**Purpose:** list files and directories for a path relative to the project root.

**Parameters:**
- `path` (`string`) — relative directory path from the project root

**Returns:**
- newline-separated directory entries
- an error message if the path does not exist, is not a directory, or is outside the allowed directory

## Agentic Loop

I will implement the loop described in the task statement.

### Planned flow

1. Start with a system prompt and the user question.
2. Send the request to the LLM together with the tool schemas.
3. If the LLM returns `tool_calls`, execute each requested tool in Python.
4. Append each tool result as a `tool` message.
5. Send the updated conversation back to the LLM.
6. Repeat until the model returns a normal text answer without `tool_calls`.
7. Stop after at most 10 tool calls.

### Tool call tracking

I will store every executed tool call in a list for the final output. Each entry will contain:

- `tool`
- `args`
- `result`

This will ensure that the final JSON includes the full reasoning trace required by the assignment.

## Output Format

The CLI must return JSON with the required fields:

- `answer` — the final answer to the user question
- `source` — the wiki section that supports the answer, for example `wiki/git-workflow.md#resolving-merge-conflicts`
- `tool_calls` — the list of executed tool calls with their arguments and results

## Path Security

Both tools must be restricted to the project directory.

### Planned protection

1. Resolve the project root once at startup.
2. Join the user-provided relative path with the project root.
3. Normalize and resolve the resulting path.
4. Verify that the resolved path is still inside the project root.
5. Reject any path that escapes the repository, including attempts such as `../secret.txt`.

This prevents path traversal and ensures the tools only access repository files.

## System Prompt Strategy

The system prompt should guide the model to behave like a documentation agent.

It will instruct the model to:

- use `list_files` to discover relevant wiki files first
- use `read_file` to inspect the most promising files
- answer only from repository documentation
- include a precise `source` reference in `file-path#section-anchor` format
- avoid guessing when the documentation does not support a claim

## Planned Changes

### `agent.py`

I will update `agent.py` to:

- define function-calling schemas for `read_file` and `list_files`
- implement secure Python handlers for both tools
- run the agentic loop
- append tool results back into the conversation
- collect all tool calls
- return final JSON with `answer`, `source`, and `tool_calls`

### `AGENT.md`

I will update `AGENT.md` to describe:

- the purpose of each tool
- how the agentic loop works
- how path traversal is prevented
- how the system pr
