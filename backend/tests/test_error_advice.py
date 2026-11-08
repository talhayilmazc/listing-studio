"""Every Etsy error the app handles reaches the seller with what happened, one action that fixes it,
and the raw answer under "Details" (etsy/advice.py)."""

from __future__ import annotations

import json

import pytest

from app.api import schemas
from app.etsy import advice
from app.etsy.errors import EtsyClientError, EtsyRateLimited, EtsyServerError, explain, record

POST_LISTING = ("POST", "/v3/application/shops/1/listings")


def _client(status: int, reason: str | None = None, method_path: tuple[str, str] = POST_LISTING) -> EtsyClientError:
    body = json.dumps({"error": reason}) if reason else None
    return EtsyClientError(status, body=body, method=method_path[0], path=method_path[1])


CASES = [
    # (the error, the action the seller is offered)
    (_client(401), "reconnect"),
    (_client(403, "Insufficient scope"), "reconnect"),
    (EtsyRateLimited(), "wait"),
    (EtsyServerError(503, method="POST", path=POST_LISTING[1]), "retry"),
    (_client(404, "Could not find readiness_state_id='9' associated with shop '1'.",
             ("PUT", "/v3/application/listings/5/inventory")), "set_up_shop"),
    (_client(400, "Could not set a calculated shipping profile to the listing. The listing is missing 'item_weight'"),
     "profiles"),
    (_client(400, "Please answer How it's made: how does your shop produce this item?"), "shop_manager"),
    (_client(400, "Personalization instructions must be 120 characters or fewer",
             ("POST", "/v3/application/listings/5/personalization")), "edit_personalization"),
    (_client(400, "Title must be 140 characters or fewer"), "edit_title"),
    (_client(400, "Tags may only contain letters, numbers and spaces"), "edit_tags"),
    (_client(400, "The image is too small", ("POST", "/v3/application/listings/5/images")), "photos"),
    (_client(404, None, ("GET", "/v3/application/listings/5")), "retry"),
    (_client(400, "Something we have never seen"), "retry"),
]


@pytest.mark.parametrize(("error", "action"), CASES)
def test_each_etsy_error_gets_its_plain_message_and_one_action(error, action) -> None:  # noqa: ANN001
    message = explain(error, "Frost Tees")
    found = advice.advise(message)
    assert found is not None and found.action == action, (message, found)
    assert found.action in advice.ACTIONS
    assert not message.lower().startswith("sorry") and "sorry" not in message.lower()


def test_details_keep_the_raw_answer_and_nothing_sent() -> None:
    error = _client(400, "Title must be 140 characters or fewer")
    error.sent = {"title": "str(152)"}  # the shape of what was sent: never shown
    out = schemas.advice_out(explain(error), record(error))
    assert out is not None
    assert out.action == "edit_title" and out.label == "Fix the title"
    assert out.details is not None
    assert "HTTP 400" in out.details and "Title must be 140 characters or fewer" in out.details
    assert "creating the draft" in out.details
    assert "str(152)" not in out.details


def test_no_message_no_advice_and_an_old_message_keeps_its_own_words() -> None:
    assert advice.advise(None) is None and schemas.advice_out(None) is None
    old = schemas.advice_out("Etsy refused a step of this draft (it answered 400) without saying why.")
    assert old is not None and old.action == "retry" and old.details.startswith("Etsy refused")


def test_every_action_with_a_page_names_it() -> None:
    for _pattern, rule in advice.RULES:
        assert rule.action in advice.ACTIONS
        assert (rule.href is not None) == (rule.action in ("reconnect", "profiles")), rule
