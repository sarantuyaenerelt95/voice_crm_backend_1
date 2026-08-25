# app/services/qpay_service.py

"""QPay V2 merchant API client.

Three calls matter: get a bearer token, create an invoice, and ask whether an
invoice was actually paid. The third one is the only thing allowed to decide
that money arrived - see verify_invoice_paid.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Optional

import redis
import requests

from app.config import settings


# QPay's token endpoint answers in about a second; invoice/check are similar.
# Kept short so a QPay outage cannot pin a web worker.
REQUEST_TIMEOUT_SEC = 20

# Re-authenticate this long before the token actually dies, so a request that
# starts just under the wire cannot land after expiry.
TOKEN_SAFETY_MARGIN_SEC = 300

ACCESS_TOKEN_CACHE_KEY = "qpay:access_token"

# Anything at or above this is a unix timestamp (~Sept 2001), not a duration:
# no token lifetime is 31 years long.
UNIX_TIMESTAMP_THRESHOLD = 1_000_000_000


class QPayError(RuntimeError):
    """QPay refused a call, or could not be reached."""


def _redis_client():
    return redis.Redis.from_url(settings.REDIS_URL)


def _api_url(path: str) -> str:
    base = settings.QPAY_BASE_URL.rstrip("/")

    # .env carries the host with no version segment; tolerate either form.
    if not base.endswith("/v2"):
        base = f"{base}/v2"

    return f"{base}/{path.lstrip('/')}"


def _require_credentials() -> None:
    missing = [
        name
        for name, value in (
            ("QPAY_CLIENT_ID", settings.QPAY_CLIENT_ID),
            ("QPAY_CLIENT_SECRET", settings.QPAY_CLIENT_SECRET),
            ("QPAY_INVOICE_CODE", settings.QPAY_INVOICE_CODE),
        )
        if not str(value or "").strip()
    ]

    if missing:
        raise QPayError(
            "QPay is not configured on this server: missing "
            + ", ".join(missing)
        )


def _token_ttl_seconds(expires_in) -> int:
    """Turn QPay's `expires_in` into seconds of remaining life.

    QPay does not follow the OAuth2 convention here: `expires_in` comes back as
    an absolute unix timestamp (confirmed against the live API - a value of
    1787730874 meant "24 hours from now", not "57 years"). Reading it as a
    duration would cache a dead token effectively forever and every call would
    start failing with 401 a day after deploy.

    Both shapes are handled: anything large enough to be a timestamp is treated
    as one, anything small is treated as a plain duration.
    """
    try:
        value = int(float(expires_in))
    except (TypeError, ValueError):
        return 0

    # Tell the two shapes apart by magnitude, not by comparing against now: a
    # timestamp that has ALREADY passed is still a timestamp, and reading it as
    # a duration would cache a dead token for decades - the exact failure this
    # function exists to prevent.
    if value >= UNIX_TIMESTAMP_THRESHOLD:
        remaining = value - int(time.time())
    else:
        remaining = value

    return max(0, remaining - TOKEN_SAFETY_MARGIN_SEC)


def fetch_access_token() -> tuple[str, int]:
    """Authenticate against QPay and return (token, seconds it stays usable)."""
    _require_credentials()

    try:
        response = requests.post(
            _api_url("auth/token"),
            auth=(settings.QPAY_CLIENT_ID, settings.QPAY_CLIENT_SECRET),
            timeout=REQUEST_TIMEOUT_SEC,
        )
    except requests.RequestException as exc:
        raise QPayError(f"Could not reach QPay to authenticate: {exc}") from exc

    if response.status_code != 200:
        raise QPayError(
            f"QPay rejected the credentials (HTTP {response.status_code}). "
            "Check QPAY_CLIENT_ID and QPAY_CLIENT_SECRET."
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise QPayError("QPay returned an unreadable auth response.") from exc

    token = str(payload.get("access_token") or "").strip()

    if not token:
        raise QPayError("QPay auth response did not contain an access token.")

    return token, _token_ttl_seconds(payload.get("expires_in"))


def get_access_token(force_refresh: bool = False) -> str:
    """A cached bearer token, shared by every worker through Redis.

    QPay asks integrators not to re-authenticate per request, and issuing a
    token per call would also be slow. Redis is already running for Celery, so
    the token is cached there rather than per-process.
    """
    client = None

    if not force_refresh:
        try:
            client = _redis_client()
            cached = client.get(ACCESS_TOKEN_CACHE_KEY)

            if cached:
                return cached.decode()

        except redis.RedisError:
            # A cache outage must not stop payments; fall through and just
            # authenticate again.
            client = None

    token, ttl = fetch_access_token()

    if ttl > 0:
        try:
            (client or _redis_client()).setex(ACCESS_TOKEN_CACHE_KEY, ttl, token)
        except redis.RedisError:
            pass

    return token


def _post(path: str, body: dict) -> dict:
    """POST to QPay with the cached token, retrying once if it was rejected."""
    for attempt in (1, 2):
        token = get_access_token(force_refresh=(attempt == 2))

        try:
            response = requests.post(
                _api_url(path),
                json=body,
                headers={"Authorization": f"Bearer {token}"},
                timeout=REQUEST_TIMEOUT_SEC,
            )
        except requests.RequestException as exc:
            raise QPayError(f"Could not reach QPay: {exc}") from exc

        # A cached token can still go stale if QPay revokes it early; one
        # forced refresh covers that without looping.
        if response.status_code in (401, 403) and attempt == 1:
            continue

        if response.status_code not in (200, 201):
            detail = (response.text or "").strip()[:300]
            raise QPayError(f"QPay call failed (HTTP {response.status_code}): {detail}")

        try:
            return response.json()
        except ValueError as exc:
            raise QPayError("QPay returned an unreadable response.") from exc

    raise QPayError("QPay kept rejecting the access token.")


def callback_signature(purchase_id: int) -> str:
    """Short HMAC proving a callback URL came from an invoice we created.

    This is only a cheap first filter against someone hitting the public
    callback with guessed purchase ids. It is NOT what authorises crediting -
    verify_invoice_paid does that by asking QPay.
    """
    return hmac.new(
        settings.SECRET_KEY.encode(),
        f"qpay-callback:{purchase_id}".encode(),
        hashlib.sha256,
    ).hexdigest()[:32]


def callback_signature_valid(purchase_id: int, signature: str) -> bool:
    return hmac.compare_digest(callback_signature(purchase_id), str(signature or ""))


def build_callback_url(purchase_id: int) -> str:
    base = settings.QPAY_CALLBACK_BASE.rstrip("/")
    signature = callback_signature(purchase_id)
    return f"{base}/payments/qpay/callback?purchase_id={purchase_id}&sig={signature}"


def create_invoice(
    purchase_id: int,
    amount_mnt: int,
    description: str,
    receiver_code: str = "terminal",
) -> dict:
    """Create a QPay invoice for one token purchase.

    Returns the raw QPay payload: invoice_id, qr_text, qr_image (base64 PNG),
    qPay_shortUrl and the list of bank app deeplinks.
    """
    _require_credentials()

    amount = int(amount_mnt)

    if amount <= 0:
        raise QPayError("Invoice amount must be greater than zero.")

    payload = _post(
        "invoice",
        {
            "invoice_code": settings.QPAY_INVOICE_CODE,
            # Our own order id, so a QPay-side report can be traced back here.
            "sender_invoice_no": str(purchase_id),
            "invoice_receiver_code": receiver_code,
            "invoice_description": description[:255],
            "amount": amount,
            "callback_url": build_callback_url(purchase_id),
        },
    )

    if not payload.get("invoice_id"):
        raise QPayError("QPay did not return an invoice id.")

    return payload


def check_payment(invoice_id: str) -> dict:
    """Ask QPay for the payments recorded against one invoice."""
    if not str(invoice_id or "").strip():
        raise QPayError("No QPay invoice id to check.")

    return _post(
        "payment/check",
        {
            "object_type": "INVOICE",
            "object_id": str(invoice_id),
            "offset": {"page_number": 1, "page_limit": 100},
        },
    )


def verify_invoice_paid(invoice_id: str, expected_amount_mnt: int) -> dict:
    """The only thing allowed to conclude that an invoice was really paid.

    QPay's callback is an unauthenticated request to a public URL, so it can
    only ever mean "go and ask". This asks, and additionally refuses to call an
    invoice settled unless the money actually covers what was ordered - a
    partial payment must not credit a full package.

    An unpaid invoice comes back as {"count": 0, "rows": []}.
    """
    payload = check_payment(invoice_id)

    rows = payload.get("rows") or []

    paid_total = 0.0

    for row in rows:
        status = str(row.get("payment_status") or "").upper()

        if status != "PAID":
            continue

        try:
            paid_total += float(row.get("payment_amount") or 0)
        except (TypeError, ValueError):
            continue

    expected = float(int(expected_amount_mnt))

    return {
        "is_paid": bool(rows) and paid_total >= expected > 0,
        "paid_amount": paid_total,
        "expected_amount": expected,
        "row_count": len(rows),
        "raw": payload,
    }


def payload_for_storage(payload: dict) -> str:
    """Trim a QPay payload down to what is worth keeping on the purchase row.

    qr_image is a ~10KB base64 PNG that can always be regenerated from qr_text,
    so it is dropped rather than stored on every order.
    """
    trimmed = {
        key: value
        for key, value in (payload or {}).items()
        if key != "qr_image"
    }

    return json.dumps(trimmed, ensure_ascii=False)[:20000]
