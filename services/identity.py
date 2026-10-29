"""Who a business is: the keys that identify it, ported from Lead Intel (packages/core/src/identity and
normalize/), so dedupe and the exclusion list match the way Lead Intel's did.

Keys, strongest first:
  place_id       Google's id for the listing
  domain         its own website's domain (never a shared platform's, never a chain's)
  phone          E.164, US by default
  name_location  normalised name + city/state, for a business with nothing better
"""

import re
import unicodedata

import phonenumbers

# ---------- Whose website is it ----------

# From Lead Intel (packages/core/src/normalize/shared-domain.ts): a listing's "website" is often a
# booking or site-builder profile, or a national chain's store page. Neither identifies the business.
PLATFORM_DOMAINS = {
    "booksy.com", "vagaro.com", "styleseat.com", "square.site", "squareup.com", "as.me",
    "acuityscheduling.com", "setmore.com", "schedulicity.com", "mindbodyonline.com", "glossgenius.com",
    "fresha.com", "simplybook.me", "zenoti.com", "janeapp.com", "mytime.com", "company.site",
    "business.site", "sites.google.com", "godaddysites.com", "wixsite.com", "weebly.com",
    "myshopify.com", "bigcartel.com", "etsy.com", "ecwid.com", "linktr.ee", "linktree.com",
    "beacons.ai", "facebook.com", "instagram.com", "yelp.com", "opentable.com", "resy.com",
    "doordash.com", "ubereats.com", "grubhub.com", "toasttab.com", "clover.com",
}  # fmt: skip
CHAIN_DOMAINS = {
    "costco.com", "walmart.com", "samsclub.com", "target.com", "targetoptical.com", "jcpenney.com",
    "jcpenneyoptical.com", "lenscrafters.com", "pearlevision.com", "visionworks.com",
    "americasbest.com", "eyemart.com", "eyemartexpress.com", "myeyedr.com", "warbyparker.com",
    "stantonoptical.com", "bjs.com", "fullypromoted.com", "michaels.com", "joann.com",
    "hobbylobby.com", "staples.com", "officedepot.com", "fedex.com", "theupsstore.com",
    "vistaprint.com", "greatclips.com", "supercuts.com", "sportclips.com", "regissalons.com",
    "bigfrog.com", "instantimprints.com", "fastsigns.com", "signarama.com", "minutemanpress.com",
    "alphagraphics.com", "customink.com",
}  # fmt: skip


def shared_domain(domain: str | None) -> str | None:
    """"platform" or "chain" when the domain isn't the business's own, else None."""
    if not domain:
        return None
    d = domain.lower().strip()
    if any(d == s or d.endswith(f".{s}") for s in PLATFORM_DOMAINS):
        return "platform"
    if any(d == s or d.endswith(f".{s}") for s in CHAIN_DOMAINS):
        return "chain"
    return None


# ---------- Normalising ----------

LEGAL_SUFFIXES = {"llc", "l l c", "inc", "incorporated", "corp", "corporation", "ltd", "limited", "llp", "lp", "pllc", "pc"}


def normalize_name(raw: str | None) -> str | None:
    """'Ink & Co., LLC' -> 'ink co'. Accents, punctuation and one trailing legal suffix go."""
    if not raw:
        return None
    s = unicodedata.normalize("NFKD", raw.lower())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"['’]", "", s)
    s = re.sub(r"[^\w\s]|_", " ", s)
    words = s.split()
    if len(words) > 1 and words[-1] in LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words) or None


def normalize_phone(raw: str | None, country: str = "US") -> str | None:
    """E.164 ('+13125550100') for a valid number, else None."""
    if not raw or not raw.strip():
        return None
    try:
        parsed = phonenumbers.parse(raw.strip(), country)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def location_key(city: str | None, state: str | None) -> str | None:
    city_n, state_n = normalize_name(city), (state or "").strip().lower()
    return f"{city_n}|{state_n}" if city_n else None


# ---------- Keys ----------

KEY_TYPES = ("place_id", "domain", "phone", "name_location", "email")


def identity_keys(
    *, place_id: str | None = None, domain: str | None = None, phone: str | None = None,
    name: str | None = None, city: str | None = None, state: str | None = None,
) -> list[tuple[str, str]]:  # fmt: skip
    """Every key this business can be recognised by, strongest first."""
    keys: list[tuple[str, str]] = []
    if place_id and place_id.strip():
        keys.append(("place_id", place_id.strip()))
    if domain and not shared_domain(domain):
        keys.append(("domain", domain.lower().strip()))
    if e164 := normalize_phone(phone):
        keys.append(("phone", e164))
    name_n, loc = normalize_name(name), location_key(city, state)
    if name_n and loc:
        keys.append(("name_location", f"{name_n}::{loc}"))
    return keys


def normalize_key(key_type: str, value: str) -> str | None:
    """A typed value as it's stored for matching, or None when it isn't a valid one."""
    value = (value or "").strip()
    if key_type == "domain":
        from services.website import normalize_domain

        return normalize_domain(value)
    if key_type == "email":
        return value.lower() if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value) else None
    if key_type == "phone":
        return normalize_phone(value)
    if key_type in ("place_id", "name_location"):
        return value or None
    return None
