# app/branding.py

"""Partner / association branding exposed to every template.

Partners are declared here as data so adding one is a one-line change plus
dropping its logo into app/static/partners/ - no markup edits.

Each logo is resolved at render time rather than hardcoded, so a partner
whose image has not been added yet degrades to a typographic wordmark
instead of a broken image, and upgrades itself the moment the file appears.
"""

from __future__ import annotations

from pathlib import Path


STATIC_DIR = Path(__file__).resolve().parent / "static"
PARTNER_DIR = STATIC_DIR / "partners"

# Extensions tried for each partner's logo, in order. SVG first so a vector
# asset wins over a raster one when both are present.
_LOGO_EXTENSIONS = (".svg", ".png", ".webp", ".jpg")

# slug -> the English name, which doubles as the translation key.
PARTNERS: tuple[tuple[str, str], ...] = (
    ("muis", "National University of Mongolia"),
)


def _logo_url(slug: str) -> str | None:
    for ext in _LOGO_EXTENSIONS:
        if (PARTNER_DIR / f"{slug}{ext}").is_file():
            return f"/static/partners/{slug}{ext}"

    return None


def partners() -> list[dict]:
    """The partner list for templates: name key, logo URL (or None), slug.

    Resolved per call rather than cached at import, so dropping a logo in
    takes effect on the next page load without restarting the app.
    """
    return [
        {
            "slug": slug,
            "name": name,
            "logo_url": _logo_url(slug),
            # Wordmark fallback: the acronym reads better than a truncated
            # full name at logo size.
            "wordmark": "МУИС" if slug == "muis" else slug.upper(),
        }
        for slug, name in PARTNERS
    ]


def install(templates) -> None:
    """Make the branding values available to every template."""
    templates.env.globals["partners"] = partners
