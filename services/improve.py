"""Suggesting a better prompt from the feedback people gave on its emails.

Claude reads the prompt version in use and the rated emails (with tags and notes), and proposes a
revised system and user prompt plus what it changed and why. Nothing is saved: a person reviews the
suggestion and saves it as a new version if they want it.
"""

from dataclasses import dataclass

import anthropic
from pydantic import BaseModel

from services.claude import error_message
from services.generation import EXAMPLE_CHARS, Example

MODEL = "claude-sonnet-5-5"

SYSTEM = """You improve prompt templates that write cold emails, using feedback from the person who
reviews the emails.

You get the current prompt (a system prompt and a user prompt template) and emails it produced,
each rated good or bad with the reviewer's tags and notes. Find what the good emails share and what
the reviewer keeps criticising, and change the prompt so future emails follow the good pattern.

Rules:
- Keep every {{placeholder}} the current prompt uses, spelled exactly the same; don't invent new ones.
- Keep the output format the prompt asks for.
- Change only what the feedback supports. Keep the rest of the wording.
- Don't copy facts about specific people or companies from the examples into the prompt.
- If the feedback is too thin or contradictory to act on, say so and return the prompt unchanged."""


class PromptSuggestion(BaseModel):
    summary: str
    changes: list[str]
    system_prompt: str
    user_prompt: str


@dataclass
class Suggestion:
    result: PromptSuggestion | None
    error: str | None
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


def _feedback_block(examples: list[Example]) -> str:
    out = []
    for i, e in enumerate(examples, 1):
        why = ", ".join(t.replace("_", " ") for t in e.tags)
        out.append(
            f"<email number=\"{i}\" rating=\"{e.rating}\">\nHook: {e.hook or '(none)'}\nSubject: {e.subject}\n"
            f"{e.body.strip()[:EXAMPLE_CHARS]}\nTags: {why or '(none)'}\nNote: {e.note.strip() or '(none)'}\n</email>"
        )
    return "\n\n".join(out)


async def suggest_prompt(
    client: anthropic.AsyncAnthropic, system_prompt: str, user_prompt: str, examples: list[Example]
) -> Suggestion:
    content = (
        f"<current_system_prompt>\n{system_prompt}\n</current_system_prompt>\n\n"
        f"<current_user_prompt>\n{user_prompt}\n</current_user_prompt>\n\n"
        f"<rated_emails>\n{_feedback_block(examples)}\n</rated_emails>\n\n"
        "Suggest the improved prompt."
    )
    try:
        response = await client.messages.parse(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM,
            output_config={"effort": "medium"},
            output_format=PromptSuggestion,
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.APIError as exc:
        return Suggestion(None, error_message(exc), MODEL)
    tokens = (response.usage.input_tokens, response.usage.output_tokens)
    if response.stop_reason == "refusal":
        return Suggestion(None, "Claude declined to suggest a prompt.", MODEL, *tokens)
    if response.stop_reason == "max_tokens" or response.parsed_output is None:
        return Suggestion(None, "Claude's suggestion couldn't be read.", MODEL, *tokens)
    return Suggestion(response.parsed_output, None, MODEL, *tokens)
