"""An email is only written when its ICP says what we sell (routers.emails.has_offer)."""

from types import SimpleNamespace

from routers.emails import has_offer


def test_an_offer_or_a_product_is_enough() -> None:
    assert has_offer(SimpleNamespace(offer="Faster quoting"), None)
    assert has_offer(SimpleNamespace(offer=""), SimpleNamespace(name="Coldbrew"))
    assert has_offer(None, SimpleNamespace(name="Coldbrew"))


def test_without_either_no_email_is_written() -> None:
    assert not has_offer(None, None)  # the ICP was never set up here
    assert not has_offer(SimpleNamespace(offer="   "), None)
