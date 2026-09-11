"""Browser Companion wait rules used by the Biedronka config flow."""

from custom_components.biedronka.auth import auth_code_from_redirect
from custom_components.biedronka.const import COMPANION_WAIT, REDIRECT_URI


def test_companion_wait_is_http_302_app_redirect():
    assert COMPANION_WAIT["event"] == "http_redirect"
    assert COMPANION_WAIT["status_codes"] == [302]
    assert COMPANION_WAIT["location_prefixes"] == [REDIRECT_URI]
    assert REDIRECT_URI.startswith("app://")


def test_code_from_companion_payload():
    captured = {
        "url": "app://cma20.biedronka.pl?code=abc.def&session_state=1",
        "query": {"code": "abc.def", "session_state": "1"},
    }
    assert captured["query"]["code"] == "abc.def"
    assert auth_code_from_redirect(captured["url"]) == "abc.def"
