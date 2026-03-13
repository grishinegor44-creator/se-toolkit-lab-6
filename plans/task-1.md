# Task 1 Plan

## LLM provider and model

I will use the Qwen Code API as an OpenAI-compatible LLM provider.

Chosen model:
- `qwen3-coder-plus`

Why this choice:
- It is the recommended option for this lab.
- It supports the OpenAI-compatible chat completions API.
- It is suitable for later tasks that will require tool calling.

The agent configuration will be stored in `.env.agent.secret` with the following variables:
- `LLM_API_KEY`
- `LLM_API_BASE`
- `LLM_MODEL`

## Agent structure

I will implement `agent.py` as a simple CLI program that:
1. Reads the user question from the first command-line argument.
2. Loads LLM settings from `.env.agent.secret`.
3. Sends the question to the LLM using the chat completions API.
4. Extracts the text response from the LLM output.
5. Prints a single JSON object to stdout.

The JSON output format will be:

```json
{
  "answer": "LLM response text",
  "tool_calls": []
}
