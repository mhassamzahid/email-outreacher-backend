"""Hook selection and email generation against a fake Claude client. No network, no cost."""

import asyncio
import functools
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import anthropic
import httpx

from services.generation import (
    MIN_MAX_TOKENS,
    EmailDraft,
    EmailWriter,
    HookChoice,
    HookPoint,
    Item,
    LeadContext,
    PromptVersionSpec,
    format_items,
)


def sync(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))

    return wrapper


ICP = {"name": "Screen printing", "industry": "Printing", "places": ["Austin, TX"]}
PROFILE = {"pains": "Slow quotes", "offer": "Faster quoting", "titles": ["Owner"], "proof": "", "cta": "Quick call?"}
HOOK = PromptVersionSpec(uuid4(), "Pick a hook.", "Posts:\n{{items}}\nOffer: {{icp.offer}}", "claude-haiku-5-5", 800)
EMAIL = PromptVersionSpec(uuid4(), "Write.", "To {{lead.firstName}} at {{lead.company}}. Hook: {{hook.text}} ({{hook.source}})",
                          "claude-sonnet-5-5", 600)  # fmt: skip
USAGE = SimpleNamespace(input_tokens=500, output_tokens=80)


def items():
    return [
        Item(uuid4(), "person", "linkedin", datetime(2026, 10, 20, tzinfo=UTC), "We just opened a second shop", "https://li/1"),
        Item(uuid4(), "company", "instagram", None, "New colours in", "https://ig/2"),
    ]


def ctx(with_items=True, instruction=""):
    lead = {"first_name": "Ava", "last_name": "Morgan", "title": "Owner", "company": "Ink Co", "website": "", "extra": {}}
    return LeadContext(ICP, PROFILE, lead, items() if with_items else [], instruction)


def fake(answers):
    """Answers each parse() call with the next (output, stop_reason), recording what was asked."""
    seen = []

    async def parse(**kwargs):
        seen.append(kwargs)
        output, stop = answers.pop(0)
        return SimpleNamespace(stop_reason=stop, usage=USAGE, parsed_output=output)

    return SimpleNamespace(messages=SimpleNamespace(parse=parse), close=lambda: asyncio.sleep(0)), seen


def hook(chosen=1, text="Congrats on the second shop", relevance=9):
    return HookChoice(
        main_points=[HookPoint(item=1, point="Opened a second shop", why="Growth means more quotes", relevance=relevance)],
        chosen_item=chosen, focus="Their second shop", hook=text, relevance=relevance, flags=[],
    )  # fmt: skip


@sync
async def test_hook_then_email_with_the_prompts_models_and_variables() -> None:
    client, seen = fake([(hook(), "end_turn"), (EmailDraft(subject=" quick idea ", body=" Hi Ava ", confidence=140), "end_turn")])
    draft = await EmailWriter("k", client=client).write(ctx(), HOOK, EMAIL)
    assert draft.error is None
    hook_call, email_call = seen
    assert hook_call["model"] == "claude-haiku-5-5" and hook_call["output_format"] is HookChoice
    assert hook_call["output_config"] == {"effort": "low"} and "temperature" not in hook_call
    assert hook_call["max_tokens"] == MIN_MAX_TOKENS  # 800 is raised: thinking counts toward it
    assert "1. [linkedin, 2026-10-20, their own post] We just opened a second shop" in hook_call["messages"][0]["content"]
    assert "2. [instagram, undated, their company's post]" in hook_call["messages"][0]["content"]
    assert email_call["model"] == "claude-sonnet-5-5" and email_call["output_config"] == {"effort": "medium"}
    assert email_call["messages"][0]["content"] == "To Ava at Ink Co. Hook: Congrats on the second shop (linkedin)"
    assert draft.chosen.url == "https://li/1" and draft.hook.relevance == 5  # clamped to 0-5
    assert (draft.email.subject, draft.email.body, draft.email.confidence) == ("quick idea", "Hi Ava", 100)
    assert [(c.step, c.model) for c in draft.calls] == [("hook", "claude-haiku-5-5"), ("email", "claude-sonnet-5-5")]


@sync
async def test_no_posts_skips_the_hook_step() -> None:
    client, seen = fake([(EmailDraft(subject="s", body="b", confidence=50), "end_turn")])
    draft = await EmailWriter("k", client=client).write(ctx(with_items=False), HOOK, EMAIL)
    assert len(seen) == 1 and draft.hook is None and draft.chosen is None and draft.email.body == "b"
    assert seen[0]["messages"][0]["content"].endswith("Hook:  ()")


@sync
async def test_a_hook_pointing_at_no_real_post_is_dropped() -> None:
    client, _ = fake([(hook(chosen=7), "end_turn"), (EmailDraft(subject="s", body="b", confidence=50), "end_turn")])
    draft = await EmailWriter("k", client=client).write(ctx(), HOOK, EMAIL)
    assert draft.chosen is None and draft.hook.hook == "" and draft.hook.chosen_item is None


@sync
async def test_reviewer_instruction_is_added_to_the_email_step() -> None:
    client, seen = fake([(hook(), "end_turn"), (EmailDraft(subject="s", body="b", confidence=50), "end_turn")])
    await EmailWriter("k", client=client).write(ctx(instruction="Shorter please"), HOOK, EMAIL)
    assert seen[1]["messages"][0]["content"].endswith("An extra instruction from the person reviewing this email: Shorter please")


@sync
async def test_running_out_of_tokens_or_refusing_is_an_error_not_a_draft() -> None:
    client, _ = fake([(hook(), "end_turn"), (None, "max_tokens")])
    draft = await EmailWriter("k", client=client).write(ctx(), HOOK, EMAIL)
    assert draft.email is None and "ran out of room in the email step" in draft.error and len(draft.calls) == 2

    client, _ = fake([(None, "refusal")])
    draft = await EmailWriter("k", client=client).write(ctx(), HOOK, EMAIL)
    assert draft.error == "Claude declined the hook step."


@sync
async def test_out_of_credit_is_said_plainly() -> None:
    async def broke(**kwargs):
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        raise anthropic.BadRequestError(
            "Your credit balance is too low to access the Anthropic API.", response=httpx.Response(400, request=request), body=None
        )

    client = SimpleNamespace(messages=SimpleNamespace(parse=broke), close=lambda: asyncio.sleep(0))
    draft = await EmailWriter("k", client=client).write(ctx(), HOOK, EMAIL)
    assert draft.error == "The Anthropic account is out of credits. Add credits in Anthropic's Plans & Billing."


def test_items_are_numbered_and_trimmed() -> None:
    long = Item(uuid4(), "person", "linkedin", None, "word " * 400, "u")
    line = format_items([long])
    assert line.startswith("1. [linkedin, undated, their own post] word") and len(line) < 760
    assert format_items([]) == "(no posts found)"


@sync
async def test_rated_examples_are_shown_in_the_email_step_only() -> None:
    from services.generation import Example

    examples = [
        Example("good", "quick idea", "Hi Sam, short and specific.", "Saw your new shop", ["send_as_is"], ""),
        Example("bad", "Partnership opportunity!!!", "Dear Sir, we are a leading agency...", "", ["too_generic", "tone_off"],
                "Reads like a template"),
    ]  # fmt: skip
    c = ctx()
    c.examples = examples
    client, seen = fake([(hook(), "end_turn"), (EmailDraft(subject="s", body="b", confidence=50), "end_turn")])
    await EmailWriter("k", client=client).write(c, HOOK, EMAIL)
    hook_prompt, email_prompt = (s["messages"][0]["content"] for s in seen)
    assert "good_example" not in hook_prompt
    assert "<good_example>\nSubject: quick idea\nHi Sam, short and specific.\nReviewer said: send as is\n</good_example>" in email_prompt
    assert "Reviewer said: too generic, tone off; Reads like a template" in email_prompt
    assert email_prompt.index("<good_example>") < email_prompt.index("<bad_example>")
    assert "Never reuse their details" in email_prompt


def test_no_examples_adds_nothing() -> None:
    from services.generation import format_examples

    assert format_examples([]) == ""
