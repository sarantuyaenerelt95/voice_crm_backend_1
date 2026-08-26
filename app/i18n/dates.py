# app/i18n/dates.py

"""Locale-aware date/time rendering, exposed to templates as the `dt` filter.

Timestamps are stored in UTC (SQLAlchemy DateTime(timezone=True)). Voicebro
only serves Mongolia, so display always converts to Asia/Ulaanbaatar (UTC+8,
no DST) rather than showing the visitor a UTC time that is 8 hours off from
their wall clock.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo


DISPLAY_TZ = ZoneInfo("Asia/Ulaanbaatar")

# Mongolian has no month names in the Latin sense - dates are written
# "<year> оны <month>-р сарын <day>", literally "the <day>th of the <month>th
# month of <year>".
_MN_DATE = "{year} оны {month}-р сарын {day}"


def _to_display_tz(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        # Naive datetimes in this codebase are already UTC (server_default
        # func.now() without a stored offset), so assume that rather than
        # silently rendering server-local time.
        value = value.replace(tzinfo=dt.timezone.utc)

    return value.astimezone(DISPLAY_TZ)


def format_datetime(value, language: str = "mn", with_time: bool = True) -> str:
    """Render a stored UTC timestamp for a human, in the given language.

    Anything that is not a real datetime (None, an empty string, a plain
    label some route already substituted) is returned unchanged, so this is
    safe to apply blindly with `{{ value | dt(lang) }}`.
    """
    if not isinstance(value, dt.datetime):
        return value

    local = _to_display_tz(value)

    if language == "mn":
        date_part = _MN_DATE.format(year=local.year, month=local.month, day=local.day)

        if not with_time:
            return date_part

        return f"{date_part}, {local.strftime('%H:%M')}"

    date_part = local.strftime("%b %-d, %Y") if hasattr(local, "strftime") else str(local)

    if not with_time:
        return date_part

    hour12 = local.strftime("%I:%M %p").lstrip("0") or "12" + local.strftime(":%M %p")

    return f"{date_part}, {hour12}"


def format_date(value, language: str = "mn") -> str:
    return format_datetime(value, language, with_time=False)


def install(templates) -> None:
    templates.env.filters["dt"] = format_datetime
    templates.env.filters["date_only"] = format_date
