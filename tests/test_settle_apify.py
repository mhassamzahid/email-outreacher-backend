"""Correcting logged Apify costs to what Apify finally charged (jobs.settle_apify_costs). No network, no database."""

import asyncio
from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace

import jobs
from config import get_settings
from services.apify import ApifyError


class FakeApify:
    def __init__(self, *args, **kwargs) -> None:
        pass

    async def get(self, path: str):
        run = path.rsplit("/", 1)[-1]
        if run == "gone":
            raise ApifyError("Apify answered 404", 404)
        return {"data": {"usageTotalUsd": {"r1": 0.00205, "r2": 0.023}[run]}}

    async def aclose(self) -> None:
        pass


def test_logged_costs_are_corrected_to_the_final_charge(monkeypatch) -> None:
    rows = [SimpleNamespace(idempotency_key="apify:run:r1", cost_usd=Decimal("0.0001")),  # charges landed after it was read
            SimpleNamespace(idempotency_key="apify:run:r2", cost_usd=Decimal("0.0230")),  # already right
            SimpleNamespace(idempotency_key="apify:run:gone", cost_usd=Decimal("0.0050"))]  # fmt: skip
    committed = []

    class Repo:
        def __init__(self, session) -> None:
            pass

        async def apify_runs_since(self, since):
            return rows

    class Session:
        async def commit(self) -> None:
            committed.append(True)

    @asynccontextmanager
    async def session():
        yield Session()

    async def dispose() -> None:
        pass

    monkeypatch.setattr(get_settings(), "apify_token", "tok")
    monkeypatch.setattr(jobs, "ApifyClient", FakeApify)
    monkeypatch.setattr(jobs, "ProviderCallRepo", Repo)
    monkeypatch.setattr(jobs, "get_sessionmaker", lambda: session)
    monkeypatch.setattr(jobs, "get_engine", lambda: SimpleNamespace(dispose=dispose))
    assert asyncio.run(jobs.settle_apify_costs()) == {"checked": 2, "corrected": 1}
    assert [r.cost_usd for r in rows] == [Decimal("0.0021"), Decimal("0.0230"), Decimal("0.0050")]  # an unreadable run is left
    assert committed == [True]
