# app/models/billing.py

from __future__ import annotations

import enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


# One token = one billable (answered) call.
MNT = "MNT"


class PurchaseStatus(str, enum.Enum):
    pending = "pending"      # order created, payment not confirmed yet
    paid = "paid"            # payment confirmed, tokens credited
    failed = "failed"        # payment attempt failed
    cancelled = "cancelled"  # abandoned / voided before payment


class LedgerEntryType(str, enum.Enum):
    purchase = "purchase"      # tokens bought and paid for            (+)
    reserve = "reserve"        # held for a dial about to be placed    (0, moves to reserved)
    commit = "commit"          # call answered, token actually spent   (-)
    release = "release"        # call not answered, hold given back    (0, leaves reserved)
    adjustment = "adjustment"  # manual admin correction               (+/-)
    refund = "refund"          # tokens returned for a paid purchase   (+/-)


class TokenPackage(Base):
    """A purchasable bundle of call tokens.

    Package 3 is open ended: call_count is null and the buyer chooses a
    quantity, charged at per_call_mnt each.
    """

    __tablename__ = "token_packages"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(30), unique=True, nullable=False)   # bagts_1 / bagts_2 / bagts_3
    name = Column(String(100), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)

    # Fixed bundles set both. Custom-quantity packages leave these null.
    call_count = Column(Integer, nullable=True)
    price_mnt = Column(BigInteger, nullable=True)

    # Unit price. For fixed bundles this is derived; for custom it is the rate.
    per_call_mnt = Column(BigInteger, nullable=False)

    # Custom-quantity packages only: minimum calls the buyer must order.
    is_custom_quantity = Column(Boolean, nullable=False, default=False)
    min_call_count = Column(Integer, nullable=True)

    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    purchases = relationship("TokenPurchase", back_populates="package")

    def __repr__(self):
        return f"<TokenPackage {self.code}>"


class TokenPurchase(Base):
    """A payment order for call tokens.

    Tokens are only credited when status becomes 'paid'. The provider_* columns
    are where a real bank/payment gateway hooks in later: store the gateway's
    invoice id in provider_ref and confirm via its callback.
    """

    __tablename__ = "token_purchases"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    package_id = Column(Integer, ForeignKey("token_packages.id"), nullable=True)

    call_count = Column(Integer, nullable=False)
    amount_mnt = Column(BigInteger, nullable=False)
    currency = Column(String(10), nullable=False, default=MNT)

    status = Column(
        Enum(PurchaseStatus),
        nullable=False,
        default=PurchaseStatus.pending,
        index=True,
    )

    # Payment gateway integration point.
    payment_provider = Column(String(50), nullable=False, default="manual")
    provider_ref = Column(String(200), nullable=True, index=True)
    provider_payload = Column(Text, nullable=True)

    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    paid_at = Column(DateTime(timezone=True), nullable=True)
    note = Column(Text, nullable=True)

    package = relationship("TokenPackage", back_populates="purchases")

    def __repr__(self):
        return f"<TokenPurchase {self.id} {self.call_count} tokens {self.status}>"


class TokenLedger(Base):
    """Immutable record of every token balance change.

    Rows are append only. The company balance columns are a fast cache of this
    ledger, and can always be rebuilt from it.
    """

    __tablename__ = "token_ledger"

    id = Column(BigInteger, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)

    entry_type = Column(Enum(LedgerEntryType), nullable=False, index=True)

    # Change to owned tokens. reserve/release move tokens in and out of the
    # reserved pool without changing what is owned, so they record 0 here.
    delta_tokens = Column(Integer, nullable=False, default=0)
    delta_reserved = Column(Integer, nullable=False, default=0)

    # Balances after this entry, for audit without replaying the whole ledger.
    tokens_after = Column(Integer, nullable=False)
    reserved_after = Column(Integer, nullable=False)

    call_log_id = Column(Integer, ForeignKey("call_logs.id"), nullable=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=True, index=True)
    purchase_id = Column(Integer, ForeignKey("token_purchases.id"), nullable=True, index=True)

    note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    def __repr__(self):
        return f"<TokenLedger {self.entry_type} {self.delta_tokens:+d} company={self.company_id}>"
