"""The Supabase Auth admin client (services/supabase_admin.py), against a fake Supabase. No network."""

import asyncio
import json
from uuid import uuid4

import httpx

from services.supabase_admin import SupabaseAdmin, SupabaseAdminError


def client(handler) -> SupabaseAdmin:
    return SupabaseAdmin("https://proj.supabase.co/", "service-key", transport=httpx.MockTransport(handler))


def test_makes_a_confirmed_account_with_the_service_key() -> None:
    seen: dict = {}
    new_id = uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(path=request.url.path, auth=request.headers["authorization"], apikey=request.headers["apikey"],
                    body=json.loads(request.content))  # fmt: skip
        return httpx.Response(200, json={"id": str(new_id), "email": "ava@example.com"})

    assert asyncio.run(client(handler).create_user("ava@example.com", "correct-horse-battery")) == new_id
    assert seen["path"] == "/auth/v1/admin/users" and seen["auth"] == "Bearer service-key" and seen["apikey"] == "service-key"
    assert seen["body"] == {"email": "ava@example.com", "password": "correct-horse-battery", "email_confirm": True}


def test_an_email_that_already_has_a_sign_in_is_left_alone() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"  # never an update or delete of someone else's account
        return httpx.Response(422, json={"code": 422, "error_code": "email_exists", "msg": "A user with this email address has already been registered"})

    assert asyncio.run(client(handler).create_user("li@example.com", "correct-horse-battery")) is None


def test_other_refusals_say_why() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"code": 422, "error_code": "weak_password", "msg": "Password is known to be weak"})

    try:
        asyncio.run(client(handler).create_user("ava@example.com", "password1234"))
    except SupabaseAdminError as exc:
        assert exc.status == 422 and "Password is known to be weak" in exc.message
    else:
        raise AssertionError("expected a refusal")
