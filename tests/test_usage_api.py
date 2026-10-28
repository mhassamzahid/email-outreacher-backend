from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from dependencies import get_provider_call_repo
from main import app

WS = {"X-Workspace-Id": str(uuid4())}


class FakeLedger:
    def __init__(self) -> None:
        self.asked = None

    async def usage(self, workspace_id, since, recent=100):
        self.asked = (workspace_id, since)
        sums = {"calls": 2, "failed": 0, "credits": Decimal("0"), "cost_usd": Decimal("0.0003"),
                "input_tokens": 1800, "output_tokens": 240}  # fmt: skip
        call = SimpleNamespace(
            idempotency_key="claude:messages:1", provider="claude", endpoint="messages", action="Research companies",
            status="succeeded", credits=None, cost_usd=Decimal("0.0003"), model="claude-haiku-5-5",
            input_tokens=1800, output_tokens=240, called_at=datetime.now(UTC),
        )  # fmt: skip
        return {
            "providers": [{"provider": "claude", **sums}],
            "actions": [{"provider": "claude", "action": "Research companies", **sums}],
            "recent": [call],
        }


def test_usage_reports_tokens_credits_and_dollars(client) -> None:
    ledger = FakeLedger()
    app.dependency_overrides[get_provider_call_repo] = lambda: ledger
    try:
        body = client.get("/api/v1/usage?days=7", headers=WS).json()
    finally:
        app.dependency_overrides.pop(get_provider_call_repo)
    assert body["providers"][0] == {"provider": "claude", "calls": 2, "failed": 0, "credits": 0.0, "costUsd": 0.0003,
                                    "inputTokens": 1800, "outputTokens": 240}  # fmt: skip
    assert body["actions"][0]["action"] == "Research companies"
    assert body["recent"][0]["id"] == "claude:messages:1" and body["recent"][0]["model"] == "claude-haiku-5-5"
    assert (datetime.now(UTC) - ledger.asked[1]).days == 7


def test_usage_needs_a_workspace(client) -> None:
    assert client.get("/api/v1/usage").status_code in (400, 422)
