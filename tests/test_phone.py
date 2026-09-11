"""Phone number normalization used by the Keycloak login form."""

import pytest

from custom_components.biedronka.auth import generate_pkce, jwt_payload, normalize_pl_phone
from custom_components.biedronka.exceptions import BiedronkaAuthError


def test_normalize_nine_digits():
    assert normalize_pl_phone("600700800") == ("600700800", "48600700800")


def test_normalize_plus_48():
    assert normalize_pl_phone("+48 600 700 800") == ("600700800", "48600700800")


def test_normalize_already_prefixed():
    assert normalize_pl_phone("48600700800") == ("600700800", "48600700800")


def test_normalize_invalid():
    with pytest.raises(BiedronkaAuthError, match="invalid_phone"):
        normalize_pl_phone("123")


def test_pkce_roundtrip():
    verifier, challenge = generate_pkce()
    assert verifier
    assert challenge
    assert verifier != challenge


def test_jwt_payload_empty():
    assert jwt_payload("not-a-jwt") == {}
