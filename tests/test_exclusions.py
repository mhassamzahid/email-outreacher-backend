"""The do-not-contact list, identity keys and contacts from websites. No network, no cost."""

from uuid import uuid4

from dependencies import get_current_user
from main import app
from services.auth import CurrentUser
from services.contacts import contact_links, extract_contacts, founder_names
from services.identity import identity_keys, normalize_key, normalize_name, normalize_phone
from tests.conftest import WORKSPACE_ID
from tests.test_leads_api import leads  # noqa: F401  (fixture)
from tests.test_pipeline_api import WS, env, find  # noqa: F401  (fixtures and helper)

# ---------- Identity ----------


def test_identity_keys_match_lead_intels() -> None:
    assert normalize_name("Ink & Co., LLC") == "ink co" and normalize_name("Café Énorme Inc") == "cafe enorme"
    assert normalize_phone("(312) 397-1220") == "+13123971220" and normalize_phone("12") is None
    keys = identity_keys(place_id="p1", domain="booksy.com", phone="(773) 303-2013", name="Method Printing", city="Chicago", state="IL")
    # A Booksy page isn't the business's own domain, so it isn't a key.
    assert keys == [("place_id", "p1"), ("phone", "+17733032013"), ("name_location", "method printing::chicago|il")]
    assert normalize_key("domain", "https://www.Ink.com/about") == "ink.com" and normalize_key("email", "not one") is None


# ---------- Contacts from a website ----------


def test_contacts_are_taken_not_guessed() -> None:
    mailtos, tels = contact_links(["mailto:Diane@InkCo.com?subject=hi", "tel:+1-312-555-0100", "/about"])
    pages = [
        {"role": "homepage", "text": "Write to info@inkco.com, or help.us@booksy.com. noreply@inkco.com. Owner: Sam Lee"},
        {"role": "about", "text": "I love the new management/owner - Happy Customer Neet"},
    ]
    found = extract_contacts(pages, mailtos, tels, [], "inkco.com")
    # The named mailbox wins; Booksy's desk isn't theirs; noreply is listed but never chosen.
    assert found.email == "diane@inkco.com" and "help.us@booksy.com" not in found.all_emails
    assert found.phone == "+1-312-555-0100" and (found.owner_name, found.owner_name_source) == ("Sam Lee", "their homepage")
    # Without the named address, the shared one; a review's "management/owner - Happy..." is no owner.
    plain = extract_contacts(pages[1:], [], [], [], "inkco.com")
    assert plain.owner_name is None and plain.empty
    ld = '<script type="application/ld+json">{"@graph":[{"@type":"LocalBusiness","founder":{"@type":"Person","name":"Diane Michaels"}}]}</script>'
    assert founder_names(ld) == ["Diane Michaels"]
    assert extract_contacts([], [], [], founder_names(ld), None).owner_name_source == "the site's own structured data"


# ---------- The list, through the API ----------


def test_manage_the_list(client) -> None:
    add = lambda **b: client.post("/api/v1/exclusions", json=b)  # noqa: E731
    r = add(keyType="domain", value="https://www.InkCo.com/", reason="current_client", businessName="Ink Co")
    assert r.status_code == 201 and r.json()["keyValue"] == "inkco.com"
    assert add(keyType="domain", value="inkco.com").status_code == 409
    assert add(keyType="email", value="nope").json()["detail"] == "That isn't an email address, like owner@inkco.com."
    assert add(keyType="phone", value="(312) 397-1220").json()["keyValue"] == "+13123971220"
    assert len(client.get("/api/v1/exclusions").json()) == 2
    assert client.delete(f"/api/v1/exclusions/{r.json()['id']}").status_code == 204
    # Sales can see the list but not change it.
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(uuid4(), "s@x.com", "S", "sales", [WORKSPACE_ID])
    assert client.get("/api/v1/exclusions").status_code == 200
    assert add(keyType="domain", value="x.com").status_code == 403


def test_excluded_companies_are_not_found(client, env, exclusions) -> None:  # noqa: F811
    client.post("/api/v1/exclusions", json={"keyType": "domain", "value": "glowmedspa.com"})
    r = find(client).json()
    # Glow Med Spa is excluded from both Lead Intel and Apollo; Ink Co still comes in from Apollo.
    assert (r["leadIntel"]["added"], r["leadIntel"]["excluded"]) == (0, 1)
    assert (r["apollo"]["added"], r["apollo"]["excluded"]) == (1, 1)


def test_exclude_a_pipeline_company(client, env, exclusions) -> None:  # noqa: F811
    find(client, sources=["apollo"])
    ink = next(c for c in env.repo.rows.values() if c.name == "Ink Co")
    r = client.post(f"/api/v1/exclusions/company/{ink.id}", json={"reason": "competitor"}, headers=WS)
    assert r.status_code == 201 and {(e["keyType"], e["keyValue"]) for e in r.json()} == {("domain", "inkco.com")}
    assert (ink.status, ink.overridden) == ("not_fit", True) and "a competitor" in ink.status_reason


def test_excluded_people_are_not_made_leads(client, leads, exclusions) -> None:  # noqa: F811
    client.post("/api/v1/exclusions", json={"keyType": "domain", "value": "inkco.com", "reason": "past_client"})
    r = client.post("/api/v1/leads", json={"email": "sam@inkco.com", "firstName": "Sam"}, headers=WS)
    assert r.status_code == 409 and r.json()["detail"] == "On the do-not-contact list: a past client (inkco.com)"
    csv = "First Name,Email\nSam,sam@inkco.com\nAva,ava@other.com\n"
    result = client.post("/api/v1/leads/import", content=csv, headers=WS | {"Content-Type": "text/csv"}).json()
    assert (result["imported"], result["invalid"]) == (1, 1)
    assert result["rows"][0]["reason"].startswith("On the do-not-contact list")
