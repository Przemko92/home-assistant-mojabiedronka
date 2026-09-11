"""Errors raised by the Biedronka client."""


class BiedronkaError(Exception):
    """Base error."""


class BiedronkaAuthError(BiedronkaError):
    """Authentication failed."""


class BiedronkaCaptchaError(BiedronkaAuthError):
    """Keycloak required Cloudflare Turnstile."""


class BiedronkaSmsBlockedError(BiedronkaAuthError):
    """SMS sending is rate-limited."""


class BiedronkaInvalidSmsError(BiedronkaAuthError):
    """SMS code was rejected."""


class BiedronkaCannotConnect(BiedronkaError):
    """Network or HTTP failure."""
