"""Hook selection, email generation and email scoring against a fake Claude client. No network, no cost."""

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
    EmailScore,
    EmailWriter,
    Example,
    HookChoice,
    HookIdea,
    Item,
    LeadContext,
    PromptVersionSpec,
    format_examples,
    format_items,
    format_rated,
    variables,
)
from services.prompts import DEFAULTS, PLACEHOLDER


def sync(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))

    return wrapper


ICP = {"name": "Screen printing", "industry": "Printing", "places": ["Austin, TX"]}
PROFILE = {"pains": "Slow quotes", "offer": "Faster quoting", "titles": ["Owner"], "proof": "", "cta": "Quick call?"}
HOOK = PromptVersionSpec(uuid4(), "Pick hooks.", "Posts:\n{{items}}\nOffer: {{icp.offer}}", "claude-haiku-5-5", 800)
EMAIL = PromptVersionSpec(uuid4(), "Write.", "To {{lead.firstName}} at {{lead.company}}. Hook: {{hook.text}} ({{hook.source}})",
                          "claude-sonnet-5-5", 600)  # fmt: skip
SCORE = PromptVersionSpec(uuid4(), "Score.", "Score {{email.subject}}: {{email.body}}\nGood:\n{{examples.good}}", "claude-haiku-5-5", 400)
USAGE = SimpleNamespace(input_tokens=500, output_tokens=80)


def items():
    return [
        Item(uuid4(), "person", "linkedin", datetime(2026, 10, 20, tzinfo=UTC), "We just opened a second shop", "https://li/1"),
        Item(uuid4(), "company", "instagram", None, "New colours in", "https://ig/2"),
    ]


def ctx(with_items=True, instruction="", company=None):
    lead = {"first_name": "Ava", "last_name": "Morgan", "title": "Owner", "company": "Ink Co", "website": "", "extra": {}}
    return LeadContext(ICP, PROFILE, lead, items() if with_items else [], instruction, company=company or {})


def fake(answers):
    """Answers each parse() call with the next (output, stop_reason), recording what was asked."""
    seen = []

    async def parse(**kwargs):
        seen.append(kwargs)
        output, stop = answers.pop(0)
        return SimpleNamespace(stop_reason=stop, usage=USAGE, parsed_output=output)

    return SimpleNamespace(messages=SimpleNamespace(parse=parse), close=lambda: asyncio.sleep(0)), seen


def idea(item=1, text="Saw your post about the second shop.", relevance=9, source="their LinkedIn post"):
    return HookIdea(item=item, source=source, point="Opened a second shop", hook=text, why="More quotes", relevance=relevance)


def hooks(*ideas, flags=()):
    return HookChoice(hooks=list(ideas) or [idea()], flags=list(flags))


@sync
async def test_hook_then_email_with_the_prompts_models_and_variables() -> None:
    client, seen = fake([(hooks(), "end_turn"), (EmailDraft(subject=" quick idea ", body=" Hi Ava ", confidence=140), "end_turn")])
    draft = await EmailWriter("k", client=client).write(ctx(), HOOK, EMAIL)
    assert draft.error is None
    hook_call, email_call = seen
    assert hook_call["model"] == "claude-haiku-5-5" and hook_call["output_format"] is HookChoice
    assert hook_call["output_config"] == {"effort": "low"} and "temperature" not in hook_call
    assert hook_call["max_tokens"] == MIN_MAX_TOKENS  # 800 is raised: thinking counts toward it
    assert "1. [linkedin, 2026-10-20, their own post] We just opened a second shop" in hook_call["messages"][0]["content"]
    assert "2. [instagram, undated, their company's post]" in hook_call["messages"][0]["content"]
    assert email_call["model"] == "claude-sonnet-5-5" and email_call["output_config"] == {"effort": "medium"}
    assert email_call["messages"][0]["content"] == "To Ava at Ink Co. Hook: Saw your post about the second shop. (linkedin)"
    assert draft.chosen.url == "https://li/1" and draft.top.relevance == 5  # clamped to 0-5
    assert (draft.email.subject, draft.email.body, draft.email.confidence) == ("quick idea", "Hi Ava", 100)
    assert [(c.step, c.model) for c in draft.calls] == [("hook", "claude-haiku-5-5"), ("email", "claude-sonnet-5-5")]


@sync
async def test_nothing_to_read_skips_the_hook_step() -> None:
    client, seen = fake([(EmailDraft(subject="s", body="b", confidence=50), "end_turn")])
    draft = await EmailWriter("k", client=client).write(ctx(with_items=False), HOOK, EMAIL)
    assert len(seen) == 1 and draft.hooks == [] and draft.top is None and draft.chosen is None and draft.email.body == "b"
    assert seen[0]["messages"][0]["content"].endswith("Hook:  ()")


@sync
async def test_the_website_alone_is_enough_for_hooks() -> None:
    site = {"company.website": "<page number=\"1\" url=\"https://inkco.example/\">We print for 40 schools</page>"}
    website_hook = idea(item=None, text="Saw you print for schools.", relevance=3, source="company website")
    client, seen = fake([(hooks(website_hook), "end_turn"), (EmailDraft(subject="s", body="b", confidence=50), "end_turn")])
    draft = await EmailWriter("k", client=client).write(ctx(with_items=False, company=site), HOOK, EMAIL)
    assert len(seen) == 2 and draft.top.hook == "Saw you print for schools." and draft.chosen is None
    assert seen[1]["messages"][0]["content"].endswith("Hook: Saw you print for schools. (company website)")


@sync
async def test_hooks_are_kept_in_their_ranked_order_and_bad_ones_dropped() -> None:
    ranked = hooks(
        idea(item=7, text="Points at a post that wasn't shown."),
        idea(item=2, text="", relevance=4),
        idea(item=2, text="Saw the new colours.", relevance=0),
        idea(item=1, text="Saw your second shop.", relevance=4),
        flags=("repost", "repost"),
    )
    client, _ = fake([(ranked, "end_turn"), (EmailDraft(subject="s", body="b", confidence=50), "end_turn")])
    draft = await EmailWriter("k", client=client).write(ctx(), HOOK, EMAIL)
    assert [h.hook for h in draft.hooks] == ["Saw the new colours.", "Saw your second shop."]
    # A hook scored 0 is unusable: the email opens with the best one that isn't.
    assert draft.top.hook == "Saw your second shop." and draft.chosen.url == "https://li/1"
    assert draft.flags == ["repost"]


@sync
async def test_reviewer_instruction_is_added_to_the_email_step() -> None:
    client, seen = fake([(hooks(), "end_turn"), (EmailDraft(subject="s", body="b", confidence=50), "end_turn")])
    await EmailWriter("k", client=client).write(ctx(instruction="Shorter please"), HOOK, EMAIL)
    assert seen[1]["messages"][0]["content"].endswith("An extra instruction from the person reviewing this email: Shorter please")


@sync
async def test_running_out_of_tokens_or_refusing_is_an_error_not_a_draft() -> None:
    client, _ = fake([(hooks(), "end_turn"), (None, "max_tokens")])
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


@sync
async def test_the_draft_is_scored_with_the_examples_and_a_failed_score_keeps_the_draft() -> None:
    c = ctx()
    c.examples = [Example("good", "quick idea", "Hi Sam, short.", "", ["send_as_is"], "")]
    client, seen = fake([
        (hooks(), "end_turn"), (EmailDraft(subject="round rock", body="Hi Ava", confidence=80), "end_turn"),
        (EmailScore(score=130, reasons=[" Close to the good example ", " "]), "end_turn"),
    ])  # fmt: skip
    draft = await EmailWriter("k", client=client).write(c, HOOK, EMAIL, SCORE)
    score_prompt = seen[2]["messages"][0]["content"]
    assert seen[2]["output_format"] is EmailScore and score_prompt.startswith("Score round rock: Hi Ava")
    assert "<good_example>\nSubject: quick idea" in score_prompt
    assert (draft.score.score, draft.score.reasons) == (100, ["Close to the good example"])

    client, _ = fake([(hooks(), "end_turn"), (EmailDraft(subject="s", body="b", confidence=50), "end_turn"), (None, "refusal")])
    draft = await EmailWriter("k", client=client).write(ctx(), HOOK, EMAIL, SCORE)
    assert draft.email.body == "b" and draft.error is None and draft.score is None
    assert draft.score_error == "Claude declined the score step."


def test_items_are_numbered_and_trimmed() -> None:
    long = Item(uuid4(), "person", "linkedin", None, "word " * 400, "u")
    line = format_items([long])
    assert line.startswith("1. [linkedin, undated, their own post] word") and len(line) < 760
    assert format_items([]) == "(no posts found)"


def test_post_groups_keep_the_numbers_of_the_full_list() -> None:
    v = variables(ctx(), items())
    assert v["posts.linkedin"] == "1. [linkedin, 2026-10-20, their own post] We just opened a second shop"
    assert v["company.posts"] == "2. [instagram, undated, their company's post] New colours in"
    assert v["posts.instagram"] == "(no posts found)"
    assert v["lead.fullName"] == "Ava Morgan" and v["examples.good"] == "(none rated yet)"


@sync
async def test_rated_examples_are_appended_unless_the_prompt_places_them() -> None:
    examples = [
        Example("good", "quick idea", "Hi Sam, short and specific.", "Saw your new shop", ["send_as_is"], ""),
        Example("bad", "Partnership opportunity!!!", "Dear Sir, we are a leading agency...", "", ["too_generic", "tone_off"],
                "Reads like a template"),
    ]  # fmt: skip
    c = ctx()
    c.examples = examples
    client, seen = fake([(hooks(), "end_turn"), (EmailDraft(subject="s", body="b", confidence=50), "end_turn")])
    await EmailWriter("k", client=client).write(c, HOOK, EMAIL)
    hook_prompt, email_prompt = (s["messages"][0]["content"] for s in seen)
    assert "good_example" not in hook_prompt
    assert "<good_example>\nSubject: quick idea\nHi Sam, short and specific.\nReviewer said: send as is\n</good_example>" in email_prompt
    assert "Reviewer said: too generic, tone off; Reads like a template" in email_prompt
    assert email_prompt.index("<good_example>") < email_prompt.index("<bad_example>")
    assert "Never reuse their details" in email_prompt

    placed = PromptVersionSpec(uuid4(), "Write.", "Good:\n{{examples.good}}\nBad:\n{{ examples.bad }}", "claude-haiku-5-5", 600)
    client, seen = fake([(hooks(), "end_turn"), (EmailDraft(subject="s", body="b", confidence=50), "end_turn")])
    await EmailWriter("k", client=client).write(c, HOOK, placed)
    email_prompt = seen[1]["messages"][0]["content"]
    assert email_prompt.count("<good_example>") == 1 and email_prompt.count("<bad_example>") == 1
    assert "Feedback from the person reviewing" not in email_prompt


def test_example_blocks() -> None:
    assert format_examples([]) == ""
    assert format_rated([], "bad") == "(none rated yet)"


def test_default_prompts_only_use_variables_that_are_filled() -> None:
    filled = set(variables(ctx(), items(), idea(), items()[0], [idea()], EmailDraft(subject="s", body="b", confidence=1)))
    filled |= {k for k in ("company.name", "company.domain", "company.location", "company.socials", "company.website",
                           "company.instagram", "company.apollo", "company.summary")}  # fmt: skip
    for kind in ("hook", "email", "score"):
        d = DEFAULTS[kind]
        used = set(PLACEHOLDER.findall(d.system_prompt + d.user_prompt))
        assert used <= filled, (kind, used - filled)
        assert "—" not in d.system_prompt + d.user_prompt and "–" not in d.system_prompt + d.user_prompt
