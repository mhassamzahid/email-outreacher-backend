"""The follow-up check as a Celery task, its beat schedule, and configurable service URLs. No Redis, no network."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

import jobs
import tasks
from celery_app import app as celery
from config import get_settings


def test_beat_starts_the_follow_up_check_on_the_configured_interval() -> None:
    entry = celery.conf.beat_schedule["follow-ups"]
    assert entry["task"] == "coldbrew.check_follow_ups" == tasks.check_follow_ups.name
    assert entry["schedule"] == float(get_settings().followup_check_seconds)
    assert entry["options"]["expires"] == entry["schedule"]  # a stale queued check is dropped


class Lock:
    def __init__(self, free: bool) -> None:
        self.free, self.released = free, False

    def acquire(self) -> bool:
        return self.free

    def release(self) -> None:
        self.released = True


@pytest.fixture
def ran(monkeypatch):
    calls = []

    async def check():
        calls.append(1)
        return jobs.CheckSummary(workspaces=1, checked=2, written=1)

    monkeypatch.setattr(jobs, "check_follow_ups", check)
    return calls


def test_the_task_runs_the_check_under_the_lock(monkeypatch, ran) -> None:
    lock = Lock(free=True)
    monkeypatch.setattr(tasks, "_lock", lambda: lock)
    out = tasks.check_follow_ups.apply().get()
    assert ran == [1] and out["written"] == 1 and lock.released


def test_a_check_already_running_means_this_one_is_skipped(monkeypatch, ran) -> None:
    monkeypatch.setattr(tasks, "_lock", lambda: Lock(free=False))
    assert tasks.check_follow_ups.apply().get() == {"skipped": True} and ran == []


def test_no_lock_without_redis(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "celery_broker_url", "memory://")
    assert tasks._lock() is None


class Engine:
    disposed = 0

    async def dispose(self):
        Engine.disposed += 1


def test_one_failing_workspace_doesnt_stop_the_others(monkeypatch) -> None:
    good, bad = uuid4(), uuid4()
    monkeypatch.setattr(get_settings(), "cheapsequencer_api_key", "k")
    monkeypatch.setattr(jobs, "get_engine", lambda: Engine())

    async def workspaces():
        return [bad, good]

    @asynccontextmanager
    async def deps_for(ws):
        yield SimpleNamespace(ws=ws)

    async def run(deps):
        if deps.ws == bad:
            raise RuntimeError("boom")
        return SimpleNamespace(checked=3, written=1, ended=1, cost_usd=0.001, notes=[])

    monkeypatch.setattr(jobs, "workspaces_in_sequence", workspaces)
    monkeypatch.setattr(jobs, "deps_for", deps_for)
    monkeypatch.setattr(jobs.outreach, "run_follow_ups", run)
    before = Engine.disposed
    s = asyncio.run(jobs.check_follow_ups())
    assert (s.workspaces, s.checked, s.written, s.failed) == (2, 3, 1, [str(bad)])
    assert Engine.disposed == before + 1  # the pool is closed, so the next run's new event loop starts clean


def test_without_cheapsequencer_nothing_runs(monkeypatch) -> None:
    monkeypatch.setattr(jobs, "get_engine", lambda: Engine())

    async def boom():
        raise AssertionError("shouldn't look for workspaces")

    monkeypatch.setattr(jobs, "workspaces_in_sequence", boom)
    assert asyncio.run(jobs.check_follow_ups()).workspaces == 0  # the autouse fixture blanks the key


@pytest.mark.parametrize(
    ("dependency", "key_setting", "url_setting", "url"),
    [
        ("get_optional_sequencer", "cheapsequencer_api_key", "cheapsequencer_api_url", "https://seq.test/v1"),
        ("get_optional_inboxes", "cheapinboxes_api_key", "cheapinboxes_api_url", "https://inboxes.test"),
        ("get_optional_apollo", "apollo_api_key", "apollo_api_url", "https://apollo.test/api/v1"),
        ("get_optional_apify", "apify_token", "apify_api_url", "https://apify.test/v2"),
    ],
)
def test_service_urls_come_from_settings(monkeypatch, dependency, key_setting, url_setting, url) -> None:
    import dependencies

    monkeypatch.setattr(get_settings(), key_setting, "k")
    monkeypatch.setattr(get_settings(), url_setting, url)

    async def first():
        gen = getattr(dependencies, dependency)()
        client = await gen.__anext__()
        base = str(client._client.base_url)
        await gen.aclose()
        return base

    assert asyncio.run(first()).rstrip("/") == url
