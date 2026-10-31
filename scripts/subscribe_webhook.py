"""Subscribe the deployed backend to CheapSequencer's reply, bounce and unsubscribe webhooks.

Run once, after the backend is live (CheapSequencer calls the address to check it before saving):

    python scripts/subscribe_webhook.py https://<your-backend>.up.railway.app

It prints the signing secret. CheapSequencer shows it only this once: put it straight into the backend's
CHEAPSEQUENCER_WEBHOOK_SECRET variable and redeploy. Until then deliveries are accepted but ignored.
Needs CHEAPSEQUENCER_API_KEY with the webhooks:write scope (and leads:read, used to match replies).

    python scripts/subscribe_webhook.py --list     what's subscribed now, without secrets
"""

import asyncio
import os
import sys
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import get_settings  # noqa: E402
from services.sending import SendingError, SequencerClient  # noqa: E402
from services.webhooks import EVENT_TYPES  # noqa: E402

PATH = "/api/v1/webhooks/cheapsequencer"


async def main(arg: str) -> int:
    settings = get_settings()
    if not settings.cheapsequencer_api_key:
        print("CHEAPSEQUENCER_API_KEY isn't set.")
        return 1
    client = SequencerClient(settings.cheapsequencer_api_key, base_url=settings.cheapsequencer_api_url)
    try:
        if arg == "--list":
            for s in await client.all("/webhook-subscriptions"):
                print(s.get("id"), s.get("status"), s.get("url"), ",".join(s.get("event_types") or []))
            return 0
        if not arg.startswith("https://"):
            print("Give the backend's public https:// address, e.g. https://coldbrew-api.up.railway.app")
            return 1
        url = arg.rstrip("/") + PATH
        created = await client._write(
            "POST", "/webhook-subscriptions", f"coldbrew-webhook-{uuid4().hex}",
            {"url": url, "description": "Coldbrew: replies, bounces, unsubscribes", "event_types": EVENT_TYPES},
        )  # fmt: skip
    except SendingError as exc:
        print(f"CheapSequencer refused: {exc.message}")
        return 1
    finally:
        await client.aclose()
    secret = next((v for k, v in created.items() if "secret" in k.lower() and isinstance(v, str)), None)
    print(f"Subscribed {url} (subscription {created.get('id')}) to: {', '.join(EVENT_TYPES)}")
    if secret:
        print("\nSigning secret (shown once; set it as CHEAPSEQUENCER_WEBHOOK_SECRET on Railway, then redeploy):\n")
        print(secret)
    else:
        print("\nNo signing secret came back. Check the subscription in CheapSequencer before relying on it.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "")))
