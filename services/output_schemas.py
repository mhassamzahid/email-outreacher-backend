"""The JSON shape each prompt kind answers in: shown read-only in the prompt editor, and sent to
Claude as the required output format (structured outputs) every time a prompt of that kind runs.

The schemas are generated from the same models the app passes to `messages.parse(output_format=...)`,
through the same conversion the Anthropic SDK applies before sending, so what's shown is what's sent.
They're fixed by the app, not by the prompt: whatever a prompt says, the answer comes back in this shape.
"""

from typing import Any

import anthropic
from pydantic import BaseModel, TypeAdapter

from services.generation import EmailDraft, EmailScore, HookChoice
from services.scoring import CompanyScore, CompanyScoreOrRead

# kind -> [(name, model, when it's used)]
OUTPUTS: dict[str, list[tuple[str, type[BaseModel], str]]] = {
    "verify": [
        (
            "Company verdict",
            CompanyScore,
            "Used when no more pages can be read (the page or time limit is reached, or the site has no other pages).",
        ),
        (
            "Company verdict, while reading the website",
            CompanyScoreOrRead,
            "Used while more pages can be read: Claude may name one more page to read before it decides.",
        ),
    ],
    "hook": [("Ranked hooks", HookChoice, "Used every time hook selection runs.")],
    "email": [("Email draft", EmailDraft, "Used every time an email is written.")],
    "score": [("Email score", EmailScore, "Used every time a draft is scored.")],
}


def sent_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The JSON schema the Anthropic SDK sends for this model."""
    return anthropic.transform_schema(TypeAdapter(model).json_schema())


def output_schemas() -> dict[str, list[dict[str, Any]]]:
    return {
        kind: [{"name": name, "note": note, "json_schema": sent_schema(model)} for name, model, note in entries]
        for kind, entries in OUTPUTS.items()
    }
