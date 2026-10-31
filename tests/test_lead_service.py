import pytest

from services.leads import (
    CsvError,
    Existing,
    clean_lead,
    duplicate_reason,
    map_headers,
    normalize_linkedin,
    parse_csv,
    remember,
    rows_to_leads,
    validate,
)


def test_headers_map_like_the_frontend() -> None:
    headers = ["First Name", "Last Name", "Work Email", "Company Name", "Job Title", "LinkedIn URL", "Instagram", "City"]
    assert map_headers(headers) == [
        "first_name", "last_name", "email", "company", "title", "linkedin_url", "instagram_url", None,
    ]  # fmt: skip


def test_template_headers_map_exactly() -> None:
    from services.leads import LEAD_FIELDS, TEMPLATE_ROWS

    assert map_headers(TEMPLATE_ROWS[0]) == list(LEAD_FIELDS)


def test_parse_csv_handles_bom_quotes_and_blank_rows() -> None:
    data = '﻿email,company\r\n"ava@x.com","Saeed & Co, Interiors"\r\n,\r\n'.encode()
    headers, rows = parse_csv(data)
    assert headers == ["email", "company"]
    assert rows == [["ava@x.com", "Saeed & Co, Interiors"]]


def test_parse_csv_reads_cp1252() -> None:
    headers, rows = parse_csv("email,company\r\nj@x.com,Caf\xe9 Rouge\r\n".encode("cp1252"))
    assert rows[0][1] == "Café Rouge"


@pytest.mark.parametrize("data", [b"", b"foo,bar\n1,2\n"])
def test_parse_csv_rejects_unreadable_files(data: bytes) -> None:
    with pytest.raises(CsvError):
        parse_csv(data)


def test_clean_lead_normalises_and_derives_website() -> None:
    lead = clean_lead({"email": " AVA@Brightloom.com ", "linkedin_url": "http://linkedin.com/in/Ava-Morgan/"})
    assert lead.email == "ava@brightloom.com"
    assert lead.website == "https://brightloom.com"
    assert lead.website_derived
    assert lead.domain == "brightloom.com"
    assert lead.linkedin_url == "https://www.linkedin.com/in/ava-morgan"


def test_free_mail_gives_no_website() -> None:
    lead = clean_lead({"email": "noor@gmail.com"})
    assert lead.website == "" and lead.domain == ""


def test_linkedin_variants_collapse_to_one() -> None:
    variants = [
        "linkedin.com/in/ava-morgan",
        "https://www.linkedin.com/in/ava-morgan/",
        "HTTP://LinkedIn.com/in/Ava-Morgan?trk=x",
    ]
    assert {normalize_linkedin(v) for v in variants} == {"https://www.linkedin.com/in/ava-morgan"}


def test_linkedin_member_ids_keep_their_case() -> None:
    """LinkedIn refuses a lowered member id ("Profile IDs are case-sensitive"), so posts came back empty."""
    url = "https://www.linkedin.com/in/ACwAAFdDy7oByil4gyhQ08GjsJk8jRQQtpnOPvc"
    assert normalize_linkedin(url + "/") == url
    assert normalize_linkedin("linkedin.com/in/ACoAAB1x2Y") == "https://www.linkedin.com/in/ACoAAB1x2Y"


def test_validate_needs_email_or_linkedin() -> None:
    assert validate(clean_lead({"company": "X"})) == "Needs an email or a LinkedIn URL"
    assert validate(clean_lead({"linkedin_url": "linkedin.com/in/x"})) is None
    assert "valid email" in validate(clean_lead({"email": "ava-at-x"}))


def test_dedupe_rules_in_order() -> None:
    seen = Existing()
    remember(clean_lead({"email": "a@x.com", "first_name": "Ava", "last_name": "M", "website": "x.com"}), seen)
    assert duplicate_reason(clean_lead({"email": "A@X.COM"}), seen) == "same email"
    remember(clean_lead({"linkedin_url": "linkedin.com/in/liam"}), seen)
    assert duplicate_reason(clean_lead({"linkedin_url": "https://www.linkedin.com/in/liam/"}), seen) == "same LinkedIn URL"
    same_person = clean_lead({"email": "ava.m@x.com", "first_name": "ava", "last_name": "m", "website": "www.x.com"})
    assert duplicate_reason(same_person, seen) == "same name at the same company domain"
    assert duplicate_reason(clean_lead({"email": "new@y.com"}), seen) is None


def test_unmapped_columns_become_extra() -> None:
    _, [lead] = rows_to_leads(["email", "City", ""], [["a@x.com", "Austin", "note"]])
    assert lead.extra == {"City": "Austin", "column_3": "note"}
