"""Making sign-in accounts in Supabase Auth, for admins adding people on the Users page.

Public sign-up is off (anyone could otherwise make an account), so accounts are made here with the
service-role key through Supabase's admin API. The sign-in is shared with Lead Intel: someone who already
has one keeps it, password included; nothing here changes or deletes an account it didn't just make.
Passwords pass straight through and are never stored or logged.
"""

from typing import Any
from uuid import UUID

import httpx


class SupabaseAdminError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def _message(response: httpx.Response) -> str:
    try:
        body: Any = response.json()
    except ValueError:
        body = {}
    detail = body.get("msg") or body.get("message") or body.get("error_description") if isinstance(body, dict) else None
    return f"Supabase couldn't make the account: {detail or f'it answered {response.status_code}'}"


class SupabaseAdmin:
    def __init__(self, url: str, service_key: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(
            base_url=url.rstrip("/") + "/auth/v1",
            headers={"apikey": service_key, "Authorization": f"Bearer {service_key}"},
            timeout=30,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def create_user(self, email: str, password: str) -> UUID | None:
        """Make a confirmed account with this password and return its id; None when the email already has
        one (it's left exactly as it is)."""
        try:
            response = await self._client.post("/admin/users", json={"email": email, "password": password, "email_confirm": True})
        except httpx.HTTPError as exc:
            raise SupabaseAdminError(f"Couldn't reach Supabase: {exc.__class__.__name__}") from exc
        if response.status_code in (409, 422):
            try:
                body = response.json() if response.content else {}
            except ValueError:
                body = {}
            code = str(body.get("error_code") or body.get("code") or "")
            text = str(body.get("msg") or body.get("message") or "").lower()
            if code == "email_exists" or "already" in text:
                return None
        if response.status_code >= 400:
            raise SupabaseAdminError(_message(response), response.status_code)
        return UUID(str(response.json()["id"]))

    async def delete_user(self, user_id: UUID) -> None:
        """Undo create_user, when saving the person here failed right after."""
        try:
            await self._client.delete(f"/admin/users/{user_id}")
        except httpx.HTTPError:
            pass  # best effort: an unused account with no Coldbrew user can't open anything here
