"""Visible message history conversion shared by conversation runtimes."""

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)


def _row_to_message(message: dict[str, str]) -> ModelMessage:
    role = message["role"]
    content = message["content"]
    if role == "user":
        return ModelRequest(parts=[UserPromptPart(content=content)])
    if role == "assistant":
        return ModelResponse(parts=[TextPart(content=content)])
    raise ValueError(f"unsupported history role: {role!r}")


def history_to_messages(history: list[dict[str, str]]) -> list[ModelMessage]:
    """Map every visible chat row, including turns after the last inbound."""
    return [_row_to_message(message) for message in history]


# Convert stored chat history into the prompt + prior messages for the LLM.
def history_to_prompt_and_messages(
    history: list[dict[str, str]],
) -> tuple[str, list[ModelMessage]]:
    """Map visible contact history to a current prompt and prior messages."""
    if not history:
        raise ValueError("history must not be empty")
    last_user = next(
        (i for i in range(len(history) - 1, -1, -1) if history[i]["role"] == "user"),
        None,
    )
    if last_user is None:
        raise ValueError("history must contain a user message")

    return history[last_user]["content"], history_to_messages(history[:last_user])
