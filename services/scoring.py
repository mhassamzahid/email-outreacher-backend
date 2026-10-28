"""Scoring a company against an ICP with Claude: how well it fits, 0 to 100, and why.

Only for companies found in Apollo (Lead Intel's are already verified). Claude reads what we
gathered (the website's text, the Instagram profile, Apollo's description) next to the ICP, and
answers in a fixed JSON shape. It's told to judge only from that evidence and to score low when
the evidence is thin, so a high score is never a guess.

Claude Haiku 5.5 by default (CLAUDE_SCORING_MODEL), at low effort: this is classification.
The SDK retries rate limits and server errors itself.
"""

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import anthropic
from pydantic import BaseModel

SYSTEM = """You assess whether a company is a good prospect for a B2B outreach campaign.

You get the campaign's ideal customer profile (ICP) and the evidence we gathered about one
company: text from its website, its Instagram profile, and Apollo's company data.

Score the fit from 0 to 100:
- 80-100: clearly the kind of business the ICP describes, with direct evidence.
- 60-79: likely a fit; the evidence points that way but leaves some doubt.
- 30-59: unclear or only partly matching.
- 0-29: a different kind of business, or one the ICP rules out.

Judge only from the evidence given. Don't assume facts that aren't there. When the evidence is
thin or missing, score lower and say what was missing. Reasons and concerns are short, specific,
and point to the evidence (for example "Website offers same-day screen printing")."""


class CompanyScore(BaseModel):
    score: int
    summary: str
    reasons: list[str]
    concerns: list[str]


@dataclass
class ScoreResult:
    score: int | None
    summary: str
    reasons: list[str]
    concerns: list[str]
    model: str
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


def build_prompt(icp: dict[str, Any], profile: dict[str, Any] | None, evidence: dict[str, Any]) -> str:
    """The ICP, then the evidence, as plain labelled JSON so nothing is lost or reworded."""
    icp_view = {
        "name": icp.get("name"),
        "industry": icp.get("industry"),
        "words_that_identify_them": icp.get("any_of") or [],
        "on_their_website": icp.get("website_any_of") or [],
        "on_their_instagram": icp.get("instagram_any_of") or [],
        "never_these": icp.get("none_of") or [],
        "searched_as": icp.get("search_terms") or [],
        "places": icp.get("places") or [],
    }
    if profile:
        icp_view |= {
            "description": profile.get("description") or "",
            "their_pains": profile.get("pains") or "",
            "what_we_offer": profile.get("offer") or "",
        }
    return (
        "<icp>\n" + json.dumps(icp_view, indent=2, ensure_ascii=False) + "\n</icp>\n\n"
        "<company_evidence>\n" + json.dumps(evidence, indent=2, ensure_ascii=False) + "\n</company_evidence>\n\n"
        "Score this company's fit with the ICP."
    )


class CompanyScorer:
    def __init__(self, api_key: str, model: str, client: anthropic.AsyncAnthropic | None = None, concurrency: int = 4) -> None:
        self._client = client or anthropic.AsyncAnthropic(api_key=api_key, max_retries=4, timeout=120.0)
        self._model = model
        self._gate = asyncio.Semaphore(concurrency)

    async def aclose(self) -> None:
        await self._client.close()

    async def score(self, icp: dict[str, Any], profile: dict[str, Any] | None, evidence: dict[str, Any]) -> ScoreResult:
        async with self._gate:
            try:
                response = await self._client.messages.parse(
                    model=self._model,
                    max_tokens=4096,
                    system=SYSTEM,
                    output_config={"effort": "low"},
                    output_format=CompanyScore,
                    messages=[{"role": "user", "content": build_prompt(icp, profile, evidence)}],
                )
            except anthropic.AuthenticationError:
                return ScoreResult(None, "", [], [], self._model, "Claude rejected the API key. Check ANTHROPIC_API_KEY.")
            except anthropic.RateLimitError:
                return ScoreResult(None, "", [], [], self._model, "Claude is rate-limiting us; try again shortly.")
            except anthropic.APIStatusError as exc:
                return ScoreResult(None, "", [], [], self._model, f"Claude answered {exc.status_code}: {exc.message}")
            except anthropic.APIConnectionError:
                return ScoreResult(None, "", [], [], self._model, "Couldn't reach Claude.")

        tokens = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
        if response.stop_reason == "refusal":
            return ScoreResult(None, "", [], [], self._model, "Claude declined to score this company.", **tokens)
        parsed = response.parsed_output
        if parsed is None:
            return ScoreResult(None, "", [], [], self._model, "Claude's answer couldn't be read.", **tokens)
        return ScoreResult(
            score=max(0, min(100, parsed.score)),
            summary=parsed.summary.strip(),
            reasons=[r.strip() for r in parsed.reasons if r.strip()][:6],
            concerns=[c.strip() for c in parsed.concerns if c.strip()][:6],
            model=self._model,
            **tokens,
        )
