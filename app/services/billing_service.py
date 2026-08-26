# app/services/billing_service.py

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException

from app import i18n
from app.models.billing import (
    LedgerEntryType,
    PurchaseStatus,
    TokenLedger,
    TokenPackage,
    TokenPurchase,
)
from app.models.call_log import CallLog
from app.models.company import Company


# Billing states stored on CallLog.token_state
TOKEN_NONE = "none"
TOKEN_RESERVED = "reserved"
TOKEN_COMMITTED = "committed"
TOKEN_RELEASED = "released"


def utc_now():
    return datetime.now(timezone.utc)


def lock_company(db, company_id: int) -> Company:
    """Fetch a company with a row lock held for the rest of the transaction.

    Every balance change goes through here. Without the lock, two workers
    dialling at the same time can both read the same balance and both spend it.
    """
    company = (
        db.query(Company)
        .filter(Company.id == company_id)
        .with_for_update()
        .first()
    )

    if not company:
        raise HTTPException(status_code=404, detail="Company not found.")

    return company


def _tokens(company: Company) -> int:
    return int(company.call_tokens or 0)


def _reserved(company: Company) -> int:
    return int(company.reserved_tokens or 0)


def spendable_tokens(company: Company) -> int:
    """Tokens available to start new calls, excluding ones already held."""
    return max(0, _tokens(company) - _reserved(company))


def get_balance(db, company_id: int) -> dict:
    company = db.query(Company).filter(Company.id == company_id).first()

    if not company:
        raise HTTPException(status_code=404, detail="Company not found.")

    return {
        "tokens": _tokens(company),
        "reserved": _reserved(company),
        "spendable": spendable_tokens(company),
    }


def _write_ledger(
    db,
    company: Company,
    entry_type: LedgerEntryType,
    delta_tokens: int = 0,
    delta_reserved: int = 0,
    call_log_id: Optional[int] = None,
    campaign_id: Optional[int] = None,
    purchase_id: Optional[int] = None,
    note: Optional[str] = None,
) -> TokenLedger:
    """Apply a balance change and record it. Never call one without the other."""
    company.call_tokens = _tokens(company) + delta_tokens
    company.reserved_tokens = _reserved(company) + delta_reserved

    entry = TokenLedger(
        company_id=company.id,
        entry_type=entry_type,
        delta_tokens=delta_tokens,
        delta_reserved=delta_reserved,
        tokens_after=company.call_tokens,
        reserved_after=company.reserved_tokens,
        call_log_id=call_log_id,
        campaign_id=campaign_id,
        purchase_id=purchase_id,
        note=note,
    )

    db.add(entry)
    return entry


# ---------------------------------------------------------------------------
# Call lifecycle: reserve -> commit (answered) or release (not answered)
# ---------------------------------------------------------------------------


def reserve_token(db, company_id: int, call: CallLog) -> bool:
    """Hold one token for a call about to be dialled.

    Returns False when there is nothing left to spend, so the caller can stop
    dialling instead of running the balance negative.
    """
    if call.token_state != TOKEN_NONE:
        # Already reserved or already settled; never double hold.
        return call.token_state == TOKEN_RESERVED

    company = lock_company(db, company_id)

    if spendable_tokens(company) < 1:
        return False

    _write_ledger(
        db,
        company,
        LedgerEntryType.reserve,
        delta_reserved=1,
        call_log_id=call.id,
        campaign_id=call.campaign_id,
        note=f"Hold for dial to {call.phone}",
    )

    call.token_state = TOKEN_RESERVED
    return True


def _company_id_for_call(db, call: CallLog) -> Optional[int]:
    """Find the owning company without depending on a loaded relationship."""
    from app.models.campaign import Campaign

    row = (
        db.query(Campaign.company_id)
        .filter(Campaign.id == call.campaign_id)
        .first()
    )

    return row[0] if row else None


def commit_token(db, call: CallLog) -> bool:
    """Spend the held token because the call was answered."""
    if call.token_state != TOKEN_RESERVED:
        return False

    company_id = _company_id_for_call(db, call)

    if not company_id:
        return False

    company = lock_company(db, company_id)

    _write_ledger(
        db,
        company,
        LedgerEntryType.commit,
        delta_tokens=-1,
        delta_reserved=-1,
        call_log_id=call.id,
        campaign_id=call.campaign_id,
        note=f"Answered call to {call.phone}",
    )

    call.token_state = TOKEN_COMMITTED
    return True


def release_token(db, call: CallLog) -> bool:
    """Give the held token back because the call was never answered."""
    if call.token_state != TOKEN_RESERVED:
        return False

    company_id = _company_id_for_call(db, call)

    if not company_id:
        return False

    company = lock_company(db, company_id)

    _write_ledger(
        db,
        company,
        LedgerEntryType.release,
        delta_reserved=-1,
        call_log_id=call.id,
        campaign_id=call.campaign_id,
        note=f"Not answered, hold returned for {call.phone}",
    )

    call.token_state = TOKEN_RELEASED
    return True


def settle_call(db, call: CallLog, answered: bool) -> bool:
    """Commit or release the hold based on whether the call was answered."""
    if answered:
        return commit_token(db, call)

    return release_token(db, call)


def release_stale_reservations(db, campaign_id: int) -> int:
    """Return holds for calls that finished without being settled.

    Safety net for a worker that died between dialling and settling, which
    would otherwise leave tokens reserved forever.
    """
    stale = (
        db.query(CallLog)
        .filter(
            CallLog.campaign_id == campaign_id,
            CallLog.token_state == TOKEN_RESERVED,
            CallLog.ended_at != None,
        )
        .all()
    )

    released = 0

    for call in stale:
        if release_token(db, call):
            released += 1

    return released


# ---------------------------------------------------------------------------
# Purchasing
# ---------------------------------------------------------------------------


def quote_package(db, package_code: str, call_count: Optional[int] = None) -> dict:
    """Work out what a purchase of this package costs."""
    package = (
        db.query(TokenPackage)
        .filter(TokenPackage.code == package_code, TokenPackage.is_active == True)
        .first()
    )

    if not package:
        raise HTTPException(status_code=404, detail="Package not found.")

    if package.is_custom_quantity:
        minimum = int(package.min_call_count or 1)

        try:
            requested = int(call_count or 0)
        except (TypeError, ValueError):
            requested = 0

        if requested < minimum:
            raise HTTPException(
                status_code=400,
                detail=i18n.Message("This package needs at least {minimum} calls.", minimum=minimum),
            )

        return {
            "package": package,
            "call_count": requested,
            "amount_mnt": requested * int(package.per_call_mnt),
        }

    return {
        "package": package,
        "call_count": int(package.call_count or 0),
        "amount_mnt": int(package.price_mnt or 0),
    }


def create_purchase(
    db,
    company_id: int,
    package_code: str,
    call_count: Optional[int] = None,
    user_id: Optional[int] = None,
    payment_provider: str = "manual",
) -> TokenPurchase:
    """Create an unpaid order. Tokens are credited later by mark_purchase_paid."""
    quote = quote_package(db, package_code, call_count)

    purchase = TokenPurchase(
        company_id=company_id,
        package_id=quote["package"].id,
        call_count=quote["call_count"],
        amount_mnt=quote["amount_mnt"],
        status=PurchaseStatus.pending,
        payment_provider=payment_provider,
        created_by_user_id=user_id,
    )

    db.add(purchase)
    db.flush()

    return purchase


def mark_purchase_paid(
    db,
    purchase_id: int,
    provider_ref: Optional[str] = None,
    provider_payload: Optional[str] = None,
) -> TokenPurchase:
    """Confirm payment and credit the tokens.

    This is the function a bank/payment gateway callback should call. It is
    idempotent, so a gateway retrying its webhook cannot credit twice.
    """
    purchase = (
        db.query(TokenPurchase)
        .filter(TokenPurchase.id == purchase_id)
        .with_for_update()
        .first()
    )

    if not purchase:
        raise HTTPException(status_code=404, detail="Purchase not found.")

    if purchase.status == PurchaseStatus.paid:
        return purchase

    if purchase.status in (PurchaseStatus.cancelled, PurchaseStatus.failed):
        raise HTTPException(
            status_code=400,
            detail=i18n.Message("Purchase is {status} and cannot be paid.", status=purchase.status.value),
        )

    company = lock_company(db, purchase.company_id)

    _write_ledger(
        db,
        company,
        LedgerEntryType.purchase,
        delta_tokens=int(purchase.call_count),
        purchase_id=purchase.id,
        note=f"Purchased {purchase.call_count} tokens for {purchase.amount_mnt} MNT",
    )

    purchase.status = PurchaseStatus.paid
    purchase.paid_at = utc_now()

    if provider_ref:
        purchase.provider_ref = provider_ref

    if provider_payload:
        purchase.provider_payload = provider_payload

    return purchase


def adjust_tokens(db, company_id: int, delta: int, note: str) -> Company:
    """Manual admin correction. Always leaves a ledger trail."""
    company = lock_company(db, company_id)

    if delta < 0 and spendable_tokens(company) < abs(delta):
        raise HTTPException(
            status_code=400,
            detail="Cannot remove more tokens than the company has available.",
        )

    _write_ledger(
        db,
        company,
        LedgerEntryType.adjustment,
        delta_tokens=int(delta),
        note=note,
    )

    return company
