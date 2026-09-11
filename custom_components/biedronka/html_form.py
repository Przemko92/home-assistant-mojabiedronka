"""Minimal HTML form parser (stdlib only)."""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin


class _FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None
        self._textarea_name: str | None = None
        self.errors: list[str] = []
        self._in_error = False
        self.title = ""
        self._in_title = False
        self.text_blobs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = {k: v for k, v in attrs}
        classes = (data.get("class") or "").lower()
        ident = (data.get("id") or "").lower()

        if tag == "title":
            self._in_title = True
        if tag == "form":
            self._current = {
                "action": data.get("action") or "",
                "method": (data.get("method") or "get").lower(),
                "id": data.get("id") or "",
                "inputs": {},
            }
            self.forms.append(self._current)
        elif tag in ("input", "button") and self._current is not None:
            name = data.get("name")
            if name:
                itype = (data.get("type") or "text").lower()
                if itype == "submit" and name not in self._current["inputs"]:
                    self._current["inputs"][name] = data.get("value") or ""
                elif itype != "submit":
                    self._current["inputs"][name] = data.get("value") or ""
        elif tag == "textarea" and self._current is not None:
            self._textarea_name = data.get("name")
        if "kc-feedback" in classes or "pf-m-error" in classes or ident.endswith("-error"):
            self._in_error = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in ("div", "span", "p", "li"):
            self._in_error = False
        if tag == "textarea":
            self._textarea_name = None

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text:
            return
        if self._in_title:
            self.title += text
        if self._textarea_name and self._current is not None:
            self._current["inputs"][self._textarea_name] = (
                self._current["inputs"].get(self._textarea_name) or ""
            ) + data
            return
        if self._in_error:
            self.errors.append(text)
        if len(text) < 200:
            self.text_blobs.append(text)


def parse_html(html: str, base_url: str) -> dict[str, Any]:
    parser = _FormParser()
    parser.feed(html)
    forms = []
    for form in parser.forms:
        action = form["action"]
        if action:
            form = {**form, "action": urljoin(base_url, action)}
        forms.append(form)
    joined = " ".join(parser.text_blobs)
    return {
        "title": parser.title,
        "forms": forms,
        "errors": parser.errors,
        "html": html,
        "has_turnstile": "cf-turnstile" in html or "challenges.cloudflare.com" in html,
        "has_phone": any("phoneNumber" in f["inputs"] for f in forms),
        "has_sms": _looks_like_sms(forms, joined),
        "sms_blocked": _looks_like_sms_blocked(joined),
        "text": joined,
    }


def _looks_like_sms(forms: list[dict[str, Any]], text: str) -> bool:
    sms_names = {"smscode", "sms_code", "code", "otp", "smsotp", "sms-code", "otc"}
    for form in forms:
        for name in form["inputs"]:
            lowered = name.lower().replace("_", "")
            if lowered in sms_names or "sms" in name.lower() or "otp" in name.lower():
                return True
    lowered = text.lower()
    return "sms" in lowered and ("kod" in lowered or "code" in lowered)


def _looks_like_sms_blocked(text: str) -> bool:
    lowered = text.lower()
    return (
        "sending verification codes has been blocked" in lowered
        or "wysyłanie kodów" in lowered
        or ("zablokowane" in lowered and "sms" in lowered)
    )


def first_form(parsed: dict[str, Any]) -> dict[str, Any] | None:
    return parsed["forms"][0] if parsed["forms"] else None
