"""The sources an ICP can use for verification and analysis, and what each costs on the Apify plan."""

from fastapi import APIRouter, Depends

from dependencies import get_optional_apify
from schemas import SourceOut, SourcesOut
from services.apify import ApifyClient, ApifyError
from services.sources import apify_prices, describe

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("", response_model=SourcesOut)
async def list_sources(apify: ApifyClient | None = Depends(get_optional_apify)) -> SourcesOut:
    """Every verification and analysis source, live or planned, with Apify's price for the account's plan
    tier (read from Apify, cached). Reading prices costs nothing."""
    if apify is None:
        return SourcesOut(tier=None, sources=[SourceOut(**s) for s in describe(None)], note="APIFY_TOKEN isn't set, so Apify prices can't be read.")
    try:
        tier, prices = await apify_prices(apify)
    except ApifyError as exc:
        return SourcesOut(tier=None, sources=[SourceOut(**s) for s in describe(None)], note=f"Couldn't read Apify prices: {exc.message}")
    return SourcesOut(tier=tier, sources=[SourceOut(**s) for s in describe(prices)])
