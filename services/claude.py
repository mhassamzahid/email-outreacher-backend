"""What every Claude call shares: readable errors and what a call cost.

Prices are USD per million tokens (input, output) at the Claude API's standard rates, for prompts of
100K tokens or fewer (ours are far smaller). An unknown model is costed as Haiku and flagged in logs.
"""

import anthropic

PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
}


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = PRICES.get(model, PRICES["claude-haiku-5-5"])
    return round((input_tokens * price_in + output_tokens * price_out) / 1e6, 6)


def error_message(exc: anthropic.APIError) -> str:
    """A sentence a person can act on, for any error the SDK raises after its own retries."""
    if isinstance(exc, anthropic.AuthenticationError):
        return "Claude rejected the API key. Check ANTHROPIC_API_KEY."
    if isinstance(exc, anthropic.RateLimitError):
        return "Claude is rate-limiting us; try again shortly."
    if isinstance(exc, anthropic.APIConnectionError):
        return "Couldn't reach Claude."
    if isinstance(exc, anthropic.APIStatusError):
        if "credit balance" in str(exc.message).lower():
            return "The Anthropic account is out of credits. Add credits in Anthropic's Plans & Billing."
        return f"Claude answered {exc.status_code}: {exc.message}"
    return f"Claude failed: {exc}"
