"""Turning CSV rows and form input into clean leads, and spotting duplicates. No database here.

Matches the frontend's import rules (lib/import.ts): the same header synonyms, lower-cased email,
URLs given a scheme, and the website taken from the email domain when none is given.
"""

import csv
import io
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

LEAD_FIELDS: dict[str, list[str]] = {
    "first_name": ["firstname", "first", "fname", "givenname"],
    "last_name": ["lastname", "last", "lname", "surname", "familyname"],
    "email": ["email", "emailaddress", "workemail", "mail", "businessemail"],
    "company": ["company", "companyname", "organization", "organisation", "account", "brand"],
    "title": ["title", "jobtitle", "position", "role", "designation"],
    "website": ["website", "url", "domain", "companywebsite", "site", "web", "companyurl"],
    "linkedin_url": ["linkedin", "linkedinurl", "linkedinprofile", "personallinkedin", "li"],
    "instagram_url": ["instagram", "instagramurl", "ig", "insta"],
    "facebook_url": ["facebook", "facebookurl", "fb", "facebookpage"],
}

TEMPLATE_ROWS = [
    list(LEAD_FIELDS),
    [
        "Ava",
        "Morgan",
        "ava@brightloom.com",
        "Brightloom",
        "Founder",
        "brightloom.com",
        "linkedin.com/in/ava-morgan",
        "instagram.com/brightloom",
        "",
    ],
]

FREE_MAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com", "yahoo.com",
    "icloud.com", "me.com", "aol.com", "proton.me", "protonmail.com",
}  # fmt: skip

EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
MAX_ROWS = 10_000


class CsvError(ValueError):
    """The file can't be read as a lead CSV at all (as opposed to individual bad rows)."""


def _normalize_header(header: str) -> str:
    return re.sub(r"[^a-z0-9]", "", header.lower())


def _score(header: str, synonyms: list[str]) -> int:
    h = _normalize_header(header)
    if not h:
        return 0
    if h in synonyms:
        return 3
    if any(len(s) > 2 and (h.startswith(s) or h.endswith(s)) for s in synonyms):
        return 2
    if any(len(s) > 3 and s in h for s in synonyms):
        return 1
    return 0


def map_headers(headers: list[str]) -> list[str | None]:
    """The lead field for each column, best match first; each field is used once. None means extra."""
    candidates = sorted(
        ((score, col, name) for col, h in enumerate(headers) for name, syn in LEAD_FIELDS.items() if (score := _score(h, syn))),
        key=lambda c: -c[0],
    )
    mapping: list[str | None] = [None] * len(headers)
    used: set[str] = set()
    for _, col, name in candidates:
        if name in used or mapping[col] is not None:
            continue
        mapping[col] = name
        used.add(name)
    return mapping


def parse_csv(data: bytes) -> tuple[list[str], list[list[str]]]:
    """Headers and data rows. Blank rows are dropped; Excel's UTF-8 BOM and cp1252 files are accepted."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
    rows = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    if not rows:
        raise CsvError("The file is empty.")
    headers, body = [h.strip() for h in rows[0]], rows[1:]
    if not any(map_headers(headers)):
        raise CsvError("No recognisable columns. The first row must be a header row, e.g. email, first_name, company.")
    if len(body) > MAX_ROWS:
        raise CsvError(f"The file has {len(body):,} rows; the limit is {MAX_ROWS:,} per import.")
    return headers, body


def normalize_url(value: str) -> str:
    v = value.strip()
    if not v:
        return ""
    return v if re.match(r"^https?://", v, re.I) else f"https://{v}"


def normalize_linkedin(value: str) -> str:
    """One spelling per profile, so the same person can't get in twice through http/www/trailing-slash variants."""
    v = value.strip()
    if not v:
        return ""
    parts = urlsplit(normalize_url(v))
    host = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/").lower()
    if not host.endswith("linkedin.com"):
        return normalize_url(v)
    return f"https://www.linkedin.com{path}"


def domain_of(url: str) -> str:
    host = urlsplit(normalize_url(url)).netloc.lower() if url else ""
    return host.removeprefix("www.").split(":")[0]


def email_domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower() if "@" in email else ""


@dataclass
class LeadInput:
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    company: str = ""
    title: str = ""
    website: str = ""
    linkedin_url: str = ""
    instagram_url: str = ""
    facebook_url: str = ""
    extra: dict[str, str] = field(default_factory=dict)
    domain: str = ""
    website_derived: bool = False

    @property
    def name_key(self) -> tuple[str, str] | None:
        """Company domain plus full name: the last dedupe rule, for leads without email or LinkedIn."""
        name = f"{self.first_name} {self.last_name}".strip().lower()
        return (self.domain, name) if self.domain and name else None


def clean_lead(raw: dict[str, str], extra: dict[str, str] | None = None) -> LeadInput:
    """Trimmed values, lower-cased email, URLs with a scheme, website from the email domain if missing."""
    value = {k: (raw.get(k) or "").strip() for k in LEAD_FIELDS}
    email = value["email"].lower()
    website = normalize_url(value["website"])
    derived = False
    if not website and email_domain(email) and email_domain(email) not in FREE_MAIL_DOMAINS:
        website, derived = f"https://{email_domain(email)}", True
    return LeadInput(
        first_name=value["first_name"],
        last_name=value["last_name"],
        email=email,
        company=value["company"],
        title=value["title"],
        website=website,
        linkedin_url=normalize_linkedin(value["linkedin_url"]),
        instagram_url=normalize_url(value["instagram_url"]),
        facebook_url=normalize_url(value["facebook_url"]),
        extra=extra or {},
        domain=domain_of(website),
        website_derived=derived,
    )


def validate(lead: LeadInput) -> str | None:
    """Why this lead can't be added, or None."""
    if not lead.email and not lead.linkedin_url:
        return "Needs an email or a LinkedIn URL"
    if lead.email and not EMAIL_PATTERN.match(lead.email):
        return f"'{lead.email}' isn't a valid email"
    return None


def rows_to_leads(headers: list[str], rows: list[list[str]]) -> tuple[list[str | None], list[LeadInput]]:
    mapping = map_headers(headers)
    leads = []
    for row in rows:
        raw: dict[str, str] = {}
        extra: dict[str, str] = {}
        for col, header in enumerate(headers):
            v = row[col].strip() if col < len(row) else ""
            if not v:
                continue
            if mapping[col]:
                raw[mapping[col]] = v
            else:
                extra[header or f"column_{col + 1}"] = v
        leads.append(clean_lead(raw, extra))
    return mapping, leads


@dataclass
class Existing:
    """What a workspace already has, in the shapes the dedupe rules compare."""

    emails: set[str] = field(default_factory=set)
    linkedins: set[str] = field(default_factory=set)
    name_keys: set[tuple[str, str]] = field(default_factory=set)


def duplicate_reason(lead: LeadInput, existing: Existing) -> str | None:
    if lead.email and lead.email in existing.emails:
        return "same email"
    if lead.linkedin_url and lead.linkedin_url.lower() in existing.linkedins:
        return "same LinkedIn URL"
    if lead.name_key and lead.name_key in existing.name_keys:
        return "same name at the same company domain"
    return None


def remember(lead: LeadInput, seen: Existing) -> None:
    if lead.email:
        seen.emails.add(lead.email)
    if lead.linkedin_url:
        seen.linkedins.add(lead.linkedin_url.lower())
    if lead.name_key:
        seen.name_keys.add(lead.name_key)
