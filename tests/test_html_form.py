"""HTML form parser used during Keycloak SMS login."""

from custom_components.biedronka.html_form import first_form, parse_html

PHONE_HTML = """
<html><title>Zaloguj się</title>
<form id="kc-form-login" action="/realms/loyalty/login-actions/authenticate?x=1" method="post">
<input id="username" name="phoneNumber" type="number" value="" />
<input name="username" type="number" hidden />
<input class="g-recaptcha cf-turnstile" name="login" type="submit" value="DALEJ" />
</form>
</html>
"""

SMS_HTML = """
<html>
<form action="/realms/loyalty/login-actions/authenticate" method="post">
<input name="smsCode" autocomplete="one-time-code" />
<input type="submit" name="login" value="Zaloguj się" />
</form>
<p>Wpisz kod SMS</p>
</html>
"""


def test_parse_phone_form():
    parsed = parse_html(PHONE_HTML, "https://konto.biedronka.pl/foo")
    assert parsed["has_phone"]
    assert parsed["has_turnstile"]
    form = first_form(parsed)
    assert form is not None
    assert "phoneNumber" in form["inputs"]
    assert form["action"].startswith("https://konto.biedronka.pl/")


def test_parse_sms_form():
    parsed = parse_html(SMS_HTML, "https://konto.biedronka.pl/")
    assert parsed["has_sms"]
    assert not parsed["has_phone"]
    form = first_form(parsed)
    assert form is not None
    assert "smsCode" in form["inputs"]
