# app/services/auth_service.py

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
from fastapi import HTTPException, status
from passlib.context import CryptContext
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models.company import Company
from app.models.user import User, UserRole


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def is_bcrypt_safe_password(password: str) -> bool:
    return len((password or "").encode("utf-8")) <= 72


def get_password_hash(password: str) -> str:
    if not password:
        raise HTTPException(status_code=400, detail="Password is required")

    if not is_bcrypt_safe_password(password):
        raise HTTPException(
            status_code=400,
            detail="Password is too long. Maximum is 72 bytes.",
        )

    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    if not plain_password or not hashed_password:
        return False

    if not is_bcrypt_safe_password(plain_password):
        return False

    try:
        return pwd_context.verify(plain_password, hashed_password)
    except Exception:
        return False


def create_access_token(data: dict) -> str:
    to_encode = data.copy()

    expire_minutes = int(getattr(settings, "ACCESS_TOKEN_EXPIRE_MINUTES", 60 * 24) or 60 * 24)
    expire = datetime.now(timezone.utc) + timedelta(minutes=expire_minutes)

    to_encode.update({"exp": expire})

    encoded_jwt = jwt.encode(
        to_encode,
        settings.SECRET_KEY,
        algorithm="HS256",
    )

    return encoded_jwt


def create_company_and_owner(
    db: Session,
    company_name: str,
    email: str,
    password: str,
) -> User:
    company_name = str(company_name or "").strip()
    email = str(email or "").strip().lower()

    if not company_name:
        raise HTTPException(status_code=400, detail="Company name is required")

    if not email:
        raise HTTPException(status_code=400, detail="Email is required")

    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=400, detail="Email already registered")

    hashed_pw = get_password_hash(password)

    try:
        new_company = Company(
            name=company_name,
            email=email,
            is_active=True,
        )

        db.add(new_company)
        db.flush()

        new_user = User(
            company_id=new_company.id,
            email=email,
            hashed_password=hashed_pw,
            # Admin of their own company - NOT UserRole.owner. Owner is the
            # platform super-admin role gating /admin/* (global SIP trunks and
            # Asterisk config reload), so it must never come from public signup.
            role=UserRole.admin,
            is_active=True,
        )

        db.add(new_user)
        db.commit()
        db.refresh(new_user)

        return new_user

    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Company or email already exists",
        )

    except HTTPException:
        db.rollback()
        raise

    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create account: {exc}",
        )


def authenticate_user(db: Session, email: str, password: str) -> User:
    email = str(email or "").strip().lower()

    user = db.query(User).filter(User.email == email).first()

    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Inactive user",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user