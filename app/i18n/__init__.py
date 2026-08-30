# app/i18n/__init__.py

"""Mongolian / English interface language.

The English source text doubles as the lookup key: `t("Create campaign")`
returns the Mongolian string when one exists and the key itself otherwise. A
missing translation therefore degrades to correct English rather than a raw
identifier leaking onto the page, which is what makes it safe to translate the
interface gradually instead of in one risky sweep.
"""

from __future__ import annotations

from typing import Optional

from app.i18n.catalog import CATALOG


DEFAULT_LANGUAGE = "en"
SUPPORTED_LANGUAGES = ("mn", "en")

LANGUAGE_NAMES = {
    "mn": "Монгол",
    "en": "English",
}

# Where the choice is remembered. A cookie rather than the session so the
# language survives logging out, and so the login page itself is already in
# the right language.
LANGUAGE_COOKIE = "voicebro_lang"
LANGUAGE_COOKIE_MAX_AGE = 60 * 60 * 24 * 365


def normalize_language(value: Optional[str]) -> Optional[str]:
    """Reduce anything user-supplied to a language we actually support."""
    code = str(value or "").strip().lower()

    if not code:
        return None

    # Accept "mn-MN", "en_US" and similar region-tagged forms.
    code = code.replace("_", "-").split("-")[0]

    return code if code in SUPPORTED_LANGUAGES else None


def language_from_accept_header(header: Optional[str]) -> Optional[str]:
    """Best supported match from an Accept-Language header.

    Entries are ranked by their q value, so a browser asking for
    "en-US,en;q=0.9,mn;q=0.8" gets English rather than whichever we check first.
    """
    if not header:
        return None

    ranked = []

    for index, part in enumerate(str(header).split(",")):
        piece = part.strip()

        if not piece:
            continue

        tag, _, params = piece.partition(";")
        quality = 1.0

        if params.strip().startswith("q="):
            try:
                quality = float(params.strip()[2:])
            except ValueError:
                quality = 0.0

        code = normalize_language(tag)

        if code:
            # index keeps the original order stable among equal q values.
            ranked.append((-quality, index, code))

    if not ranked:
        return None

    ranked.sort()
    return ranked[0][2]


def resolve_language(
    explicit: Optional[str] = None,
    cookie: Optional[str] = None,
    accept_header: Optional[str] = None,
) -> str:
    """Decide which language to render in, most specific source winning."""
    for candidate in (
        normalize_language(explicit),
        normalize_language(cookie),
        language_from_accept_header(accept_header),
    ):
        if candidate:
            return candidate

    return DEFAULT_LANGUAGE


class Message(str):
    """An English message that remembers the key and values it was built from.

    Server-side messages are raised far from any request, so they cannot know
    the reader's language. Subclassing str means the message behaves as plain
    English everywhere it already flows - logs, API responses, f-strings - while
    the template layer can still recover the key and translate it.

        raise HTTPException(400, detail=Message("This package needs at least {minimum} calls.", minimum=10))
    """

    def __new__(cls, key: str, **params):
        text = key
        if params:
            try:
                text = key.format(**params)
            except (KeyError, IndexError, ValueError):
                text = key
        obj = super().__new__(cls, text)
        obj.key = key
        obj.params = params
        return obj


def translate(text: str, language: str) -> str:
    """Look up one string. Unknown text falls back to the English key."""
    if language == "en":
        return text

    entry = CATALOG.get(text)

    if not entry:
        return text

    return entry.get(language) or text


def translator(language: str):
    """A `t` bound to one language, for putting in the template globals."""
    def t(text, **kwargs) -> str:
        # A Message carries the untranslated key and its values; a plain string
        # is its own key.
        key = getattr(text, "key", text)
        kwargs = {**getattr(text, "params", {}), **kwargs}

        rendered = translate(key, language)

        # Only format when asked, so a stray brace in ordinary copy is safe.
        if kwargs:
            try:
                return rendered.format(**kwargs)
            except (KeyError, IndexError, ValueError):
                return rendered

        return rendered

    return t


def missing_translations() -> list[str]:
    """Catalog keys with no Mongolian yet - used by the coverage test."""
    return [
        key
        for key, entry in CATALOG.items()
        if not str(entry.get("mn") or "").strip()
    ]
