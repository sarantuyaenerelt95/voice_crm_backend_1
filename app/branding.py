# app/branding.py

"""Partner / association branding exposed to every template.

The МУИС logo is an image file dropped into app/static/partners/. It is
looked up at render time rather than hardcoded so the page degrades to a
typographic wordmark when the file is absent, instead of showing a broken
image icon - and upgrades itself the moment the real asset is added, with
no code change.
"""

from __future__ import annotations

from pathlib import Path


STATIC_DIR = Path(__file__).resolve().parent / "static"
PARTNER_DIR = STATIC_DIR / "partners"

# Contact number shown alongside the partner strip.
CONTACT_PHONE = "88997936"

# Checked in order; the first that exists wins. SVG first so a vector logo is
# preferred over a raster one when both are present.
_LOGO_CANDIDATES = ("muis.svg", "muis.png", "muis.webp", "muis.jpg")


def partner_logo_url() -> str | None:
    """Public URL of the МУИС logo, or None if the asset has not been added.

    Resolved per call rather than cached at import, so dropping the file in
    takes effect on the next page load without restarting the app.
    """
    for name in _LOGO_CANDIDATES:
        if (PARTNER_DIR / name).is_file():
            return f"/static/partners/{name}"

    return None


def install(templates) -> None:
    """Make the branding values available to every template."""
    templates.env.globals["partner_logo_url"] = partner_logo_url
    templates.env.globals["contact_phone"] = CONTACT_PHONE
