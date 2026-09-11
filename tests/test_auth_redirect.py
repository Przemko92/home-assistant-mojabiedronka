"""OAuth redirect parsing for browser login."""

import pytest

from custom_components.biedronka.auth import (
    auth_code_from_redirect,
    authorization_url,
    generate_pkce,
)
from custom_components.biedronka.exceptions import BiedronkaAuthError


def test_auth_code_from_app_url():
    assert (
        auth_code_from_redirect(
            "app://cma20.biedronka.pl?code=abc.def&session_state=1"
        )
        == "abc.def"
    )


def test_auth_code_from_noisy_copy():
    text = "Failed to launch 'app://cma20.biedronka.pl?code=xyz123&iss=https%3A%2F%2Fkonto.biedronka.pl'"
    assert auth_code_from_redirect(text) == "xyz123"


def test_auth_code_raw():
    assert auth_code_from_redirect("  onlyTheCode  ") == "onlyTheCode"


def test_auth_code_empty():
    with pytest.raises(BiedronkaAuthError, match="missing_auth_code"):
        auth_code_from_redirect("   ")


def test_authorization_url_contains_pkce():
    _, challenge = generate_pkce()
    url = authorization_url(challenge)
    assert "konto.biedronka.pl/realms/loyalty/protocol/openid-connect/auth" in url
    assert "client_id=cma20" in url
    assert "code_challenge_method=S256" in url
    assert challenge in url
