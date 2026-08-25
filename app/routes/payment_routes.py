# app/routes/payment_routes.py

"""Public payment-gateway endpoints.

These live outside /web on purpose: QPay's servers call them, and anything
under /web is redirected to the login page by the session middleware.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.billing import PurchaseStatus, TokenPurchase
from app.services import billing_service, qpay_service


router = APIRouter(prefix="/payments", tags=["payments"])


def settle_qpay_purchase(db: Session, purchase: TokenPurchase) -> dict:
    """Ask QPay whether this order was really paid, and credit it if so.

    Everything that can conclude a purchase is paid funnels through here:
    QPay's callback, the buyer's browser polling the payment page, and the
    "check now" button. mark_purchase_paid is idempotent, so all three racing
    at once still credits exactly once.
    """
    if purchase.status == PurchaseStatus.paid:
        return {"status": "paid", "credited": False}

    if purchase.status in (PurchaseStatus.cancelled, PurchaseStatus.failed):
        return {"status": purchase.status.value, "credited": False}

    if not purchase.provider_ref:
        return {"status": "pending", "credited": False, "detail": "No QPay invoice yet."}

    result = qpay_service.verify_invoice_paid(
        invoice_id=purchase.provider_ref,
        expected_amount_mnt=int(purchase.amount_mnt),
    )

    if not result["is_paid"]:
        return {
            "status": "pending",
            "credited": False,
            "paid_amount": result["paid_amount"],
        }

    billing_service.mark_purchase_paid(
        db,
        purchase.id,
        provider_ref=purchase.provider_ref,
        provider_payload=qpay_service.payload_for_storage(result["raw"]),
    )
    db.commit()

    return {"status": "paid", "credited": True}


@router.api_route("/qpay/callback", methods=["GET", "POST"])
async def qpay_callback(
    request: Request,
    purchase_id: int = Query(...),
    sig: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """QPay pings this when an invoice is paid.

    The ping itself proves nothing - it is an unauthenticated request to a URL
    anyone can hit - so it is treated purely as "go and check". The signature
    is a cheap filter against someone spraying guessed purchase ids; the actual
    authority is QPay's own payment/check call inside settle_qpay_purchase.
    """
    if not qpay_service.callback_signature_valid(purchase_id, sig):
        return JSONResponse(status_code=403, content={"detail": "Invalid callback signature."})

    purchase = db.query(TokenPurchase).filter(
        TokenPurchase.id == purchase_id,
    ).first()

    if not purchase:
        return JSONResponse(status_code=404, content={"detail": "Purchase not found."})

    try:
        result = settle_qpay_purchase(db, purchase)

    except Exception as exc:
        db.rollback()
        print(f"QPAY CALLBACK ERROR: purchase_id={purchase_id}: {exc}")

        # A 500 tells QPay to retry later, which is what we want when our own
        # side failed: the payment is real, we just could not confirm it yet.
        return JSONResponse(
            status_code=500,
            content={"detail": "Could not verify the payment, please retry."},
        )

    print(
        f"QPAY CALLBACK: purchase_id={purchase_id}, "
        f"status={result['status']}, credited={result['credited']}"
    )

    return JSONResponse(status_code=200, content={"status": result["status"]})
