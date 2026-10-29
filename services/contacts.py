"""Contact details from a company's own website, ported from Lead Intel
(packages/core/src/checks/contacts.ts and parse.ts).

Research already reads the homepage and the pages Claude picks; this takes what they publish: email
addresses (mailto links and text), a phone number (tel links), and the owner's name, but only where
the page says outright that this person owns or founded the business. Nothing is guessed.
"""

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import unquote

from services.identity import shared_domain

# Machines and mailing lists: real addresses, but nobody answers them.
NON_HUMAN = {"noreply", "no-reply", "donotreply", "do-not-reply", "postmaster", "abuse", "webmaster", "privacy",
             "unsubscribe", "bounce", "mailer-daemon"}  # fmt: skip
# Shared mailboxes, best first. A named one (diane@) beats all of these.
SHARED = ["hello", "info", "contact", "sales", "enquiries", "inquiries", "office", "admin", "orders"]

EMAIL_TEXT = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# Written without re.I on purpose: the name must start with a capital. The lookbehind stops
# "management/owner - Happy Customer" in a review being read as an owner called "Happy Customer".
LABELLED_OWNER = re.compile(
    r"(?<![\w/])(?:[Cc]o-)?(?:[Oo]wner|[Ff]ounder|[Pp]roprietor|[Pp]rincipal|[Mm]anaging [Dd]irector)\s*(?:and\s+\w+\s*)?"
    r"[:\-–—]\s*([A-Z][a-zA-Z'’-]+(?:\s+[A-Z][a-zA-Z'’-]+){0,2})"
)
FOUNDED_BY = re.compile(r"\b(?:[Ff]ounded|[Oo]wned|[Ss]tarted|[Rr]un)\s+by\s+([A-Z][a-zA-Z'’-]+(?:\s+[A-Z][a-zA-Z'’-]+){0,2})")
NOT_NAMES = {
    "The", "Our", "We", "Us", "This", "That", "A", "An", "And", "Or", "For", "With", "Owner", "Founder", "Team",
    "Company", "Business", "Family", "Staff", "Management", "Customer", "Customers", "Client", "Clients", "Happy",
    "Satisfied", "Verified", "Reviewer", "Guest", "Visitor", "Anonymous", "Reviews", "Review", "Testimonial",
}  # fmt: skip
LD_JSON = re.compile(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", re.I | re.S)


@dataclass
class SiteContacts:
    email: str | None = None  # the best one to write to
    all_emails: list[str] = field(default_factory=list)
    phone: str | None = None
    owner_name: str | None = None
    owner_name_source: str | None = None  # where it was stated, so a person can judge it

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def empty(self) -> bool:
        return not (self.all_emails or self.phone or self.owner_name)


# ---------- From the HTML: links and structured data ----------


def contact_links(hrefs: list[str]) -> tuple[list[str], list[str]]:
    """(mailto addresses, tel numbers) from a page's links."""
    mailtos, tels = [], []
    for href in hrefs:
        h = href.strip()
        if h.lower().startswith("mailto:"):
            mailtos.append(unquote(h[7:]).split("?")[0].strip().lower())
        elif h.lower().startswith("tel:"):
            number = unquote(h[4:]).strip()
            if len(number) >= 7:
                tels.append(number)
    return mailtos, tels


def _person_names(value: Any, into: list[str]) -> None:
    if isinstance(value, list):
        for item in value:
            _person_names(item, into)
    elif isinstance(value, str) and value.strip():
        into.append(value.strip())
    elif isinstance(value, dict) and isinstance(value.get("name"), str):
        into.append(value["name"].strip())


def _walk(node: Any, into: list[str]) -> None:
    if isinstance(node, list):
        for item in node:
            _walk(item, into)
    elif isinstance(node, dict):
        # `founder` is the one schema.org relation that means "this person started this business".
        for key in ("founder", "founders"):
            if key in node:
                _person_names(node[key], into)
        if isinstance(node.get("@graph"), list):
            _walk(node["@graph"], into)


def founder_names(html: str) -> list[str]:
    names: list[str] = []
    for block in LD_JSON.findall(html or ""):
        try:
            _walk(json.loads(block.strip()), names)
        except ValueError:
            continue
    return list(dict.fromkeys(names))


# ---------- Choosing ----------


def _usable(address: str) -> bool:
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", address):
        return False
    if re.search(r"@\dx\b", address) or re.search(r"\.(png|jpe?g|gif|webp|svg|css|js)$", address):
        return False
    if re.match(r"^(you|your|name|email|username|user|someone)@(example|domain|yourdomain|email|sentry)\.", address):
        return False
    # A real address, but a platform's: help@booksy.com is Booksy's desk, not the salon's.
    return shared_domain(address.split("@")[1]) is None


def _best_email(emails: list[str], domain: str | None) -> str | None:
    usable = [e for e in emails if e.split("@")[0] not in NON_HUMAN]
    if not usable:
        return None
    own = domain.removeprefix("www.").lower() if domain else None
    pool = [e for e in usable if own and e.split("@")[1].endswith(own)] or usable
    named = next((e for e in pool if (m := e.split("@")[0]) not in SHARED and "." not in m and len(m) >= 3), None)
    if named:
        return named
    for mailbox in SHARED:
        if found := next((e for e in pool if e.split("@")[0] == mailbox), None):
            return found
    return pool[0]


def _plausible_name(value: str) -> bool:
    words = value.strip().split()
    if not 1 <= len(words) <= 3 or not 2 <= len(value.strip()) <= 60:
        return False
    return all(w not in NOT_NAMES and re.fullmatch(r"[A-Z][a-zA-Z'’-]*", w) for w in words)


def extract_contacts(
    pages: list[dict], mailtos: list[str], tels: list[str], owner_names: list[str], domain: str | None
) -> SiteContacts:
    """The contacts a site publishes. `pages` are [{role, text}] as research stores them."""
    emails = [e for e in mailtos if _usable(e)]
    for page in pages:
        emails += [m.lower() for m in EMAIL_TEXT.findall(page.get("text") or "") if _usable(m.lower())]
    emails = list(dict.fromkeys(emails))

    owner, source = None, None
    if structured := next((n for n in owner_names if _plausible_name(n)), None):
        owner, source = structured, "the site's own structured data"
    else:
        for page in pages:
            for pattern in (LABELLED_OWNER, FOUNDED_BY):
                match = pattern.search(page.get("text") or "")
                if match and _plausible_name(match.group(1)):
                    owner = match.group(1).strip()
                    role = page.get("role") or "page"
                    source = "their homepage" if role == "homepage" else f"their {role} page"
                    break
            if owner:
                break

    return SiteContacts(
        email=_best_email(emails, domain), all_emails=emails, phone=next(iter(tels), None), owner_name=owner, owner_name_source=source
    )
