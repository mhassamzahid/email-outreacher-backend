"""What the email step knows about a lead (routers/emails.py): reposts, their headline, and the company website."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

from routers.emails import _own_headline, _read_site, reposted_from
from services.generation import Item, format_items


def post(lead=True, source="linkedin", **raw):
    return SimpleNamespace(lead_id=uuid4() if lead else None, source=source, is_repost=False, raw=raw)


KEVIN = {"name": "Kevin Alsterda", "info": "Founder, CEO at UpMerch"}
REPOST = {"author": {"name": "Martin Gould", "info": "Personal Injury Attorney"}, "repostedBy": {"name": "Kevin Alsterda"},
          "header": {"text": "Kevin Alsterda reposted this"}}  # fmt: skip


def test_a_repost_is_someone_elses_words_and_is_labelled_so() -> None:
    assert reposted_from(post(author=KEVIN, header={"text": None})) == ""
    assert reposted_from(post(**REPOST)) == "Martin Gould, Personal Injury Attorney"
    line = format_items([Item(uuid4(), "person", "linkedin", None, "GGH is hiring", "u", "Martin Gould, Personal Injury Attorney")])
    assert "they reposted this post by Martin Gould" in line and "their own post" not in line


def test_their_headline_comes_from_their_own_posts_not_reposts() -> None:
    assert _own_headline([post(**REPOST), post(author=KEVIN)]) == "Founder, CEO at UpMerch"
    assert _own_headline([post(**REPOST), post(lead=False, author={"info": "A company"})]) == ""


class FakeReader:
    def __init__(self) -> None:
        self.reads: list[str] = []

    async def read(self, domain, max_pages=6):
        self.reads.append(domain)
        return SimpleNamespace(reached=True, via="own_crawl", pages=[{"url": f"https://{domain}/", "role": "home", "text": "We print merch"}])


class FakeCompanies:
    def __init__(self) -> None:
        self.saved: list = []

    async def save_pages(self, workspace_id, company_id, via, pages):
        self.saved.append((company_id, via, len(pages)))


def test_a_site_nobody_read_is_read_once_and_kept_on_the_company() -> None:
    reader, companies, cache = FakeReader(), FakeCompanies(), {}
    company = SimpleNamespace(id=uuid4(), domain="upmerch.com")
    lead = SimpleNamespace(domain=None, website="")
    for _ in range(2):  # two leads at the same company
        pages = asyncio.run(_read_site(uuid4(), company, lead, reader, companies, cache))
    assert pages == [{"url": "https://upmerch.com/", "text": "We print merch"}]
    assert reader.reads == ["upmerch.com"] and companies.saved == [(company.id, "own_crawl", 1)]
    # No company and no website: nothing to read.
    assert asyncio.run(_read_site(uuid4(), None, SimpleNamespace(domain=None, website=""), reader, companies, cache)) == []
