"""People from Apollo-format CSVs. No database."""

import pytest

from services.leads import CsvError
from services.people_csv import TEMPLATE_HEADERS, map_headers, parse_people_csv, template_csv

APOLLO = (
    "First Name,Last Name,Title,Company,Company Name for Emails,Email,Email Status,Seniority,Person Linkedin Url,"
    "Website,City,State,Country,Apollo Contact Id,# Employees\n"
)


def test_template_has_apollos_columns_and_reads_back() -> None:
    parsed = parse_people_csv(template_csv())
    assert TEMPLATE_HEADERS[:4] == ["First Name", "Last Name", "Title", "Company Name"]
    [(row, ava)] = parsed.people
    assert row == 2 and (ava.first_name, ava.email, ava.seniority) == ("Ava", "ava@brightloom.example", "founder")


def test_apollo_export_maps_and_keeps_unused_columns() -> None:
    text = APOLLO + (
        "Sam,Lee,Owner,Ink Co,Ink Co LLC,SAM@INKCO.EXAMPLE,Verified,Owner,linkedin.com/in/sam-lee,inkco.example,"
        "Austin,Texas,United States,64ab1,12\n"
    )
    parsed = parse_people_csv(text)
    [(_, sam)] = parsed.people
    assert (sam.company, sam.email, sam.email_status, sam.domain) == ("Ink Co", "sam@inkco.example", "verified", "inkco.example")
    assert sam.linkedin_url.startswith("https://") and sam.external_id == ("apollo", "64ab1")
    assert sam.extra == {"Company Name for Emails": "Ink Co LLC", "# Employees": "12"}
    assert parsed.columns["Company"] == "company"


def test_bad_rows_are_reported_and_repeats_imported_once() -> None:
    text = APOLLO + ",,,,,,,,,,,,,,\n" + "Al,,,,,not-an-email,,,,,,,,,\n" + "Bo,Ng,,Ink Co,,,,,,,,,,,\n" + "Bo,Ng,,Ink Co,,,,,,,,,,,\n"
    parsed = parse_people_csv(text)
    assert parsed.errors == [(3, "Email isn't valid: not-an-email")]
    assert len(parsed.people) == 1 and parsed.duplicates == 1
    assert parsed.people[0][1].external_id == ("manual", "name:bo ng@ink co")


def test_no_name_columns_is_refused() -> None:
    with pytest.raises(CsvError):
        parse_people_csv("Email\nsam@inkco.example\n")


def test_company_prefers_the_plain_name() -> None:
    assert map_headers(["Company Name for Emails", "Company"]) == [None, "company"]
