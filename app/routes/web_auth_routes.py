# app/routes/web_auth_routes.py

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from passlib.context import CryptContext

from app.config import settings
from app.database import get_db
from app.i18n import templating as i18n_templating
from app.i18n import dates as i18n_dates
from app import branding
from app.i18n.templating import request_language
from app.models.user import User
from app.models.company import Company
from app.services.email_service import send_password_reset_email


router = APIRouter(prefix="/web", tags=["web-auth"])
templates = Jinja2Templates(directory="app/templates")

# Gives every template t(), lang and languages.
i18n_templating.install(templates)
i18n_dates.install(templates)
branding.install(templates)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def model_columns(model):
    return {column.name for column in model.__table__.columns}


def user_email_column():
    cols = model_columns(User)

    if "email" in cols:
        return "email"

    if "username" in cols:
        return "username"

    raise RuntimeError("User model must have email or username column")


def user_password_column():
    cols = model_columns(User)

    if "hashed_password" in cols:
        return "hashed_password"

    if "password_hash" in cols:
        return "password_hash"

    if "password" in cols:
        return "password"

    raise RuntimeError("User model must have hashed_password, password_hash, or password column")


def is_bcrypt_safe_password(password: str) -> bool:
    return len((password or "").encode("utf-8")) <= 72


def hash_password(password: str) -> str:
    if not is_bcrypt_safe_password(password):
        raise ValueError("Password is too long. Maximum is 72 bytes.")
    return pwd_context.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    if not password or not hashed_password:
        return False

    if not is_bcrypt_safe_password(password):
        return False

    try:
        return pwd_context.verify(password, hashed_password)
    except Exception:
        return False


def normalized_role(user: User) -> str:
    role_raw = getattr(user, "role", "")

    if hasattr(role_raw, "value"):
        role_value = role_raw.value
    else:
        role_value = str(role_raw or "")

    role_value = role_value.lower().strip()

    if "." in role_value:
        role_value = role_value.split(".")[-1]

    return role_value


def get_logged_in_web_user(request: Request, db: Session) -> User:
    user_id = request.session.get("user_id")

    if not user_id:
        raise HTTPException(status_code=401, detail="Not logged in")

    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Invalid session")

    return user


@router.get("/", response_class=HTMLResponse)
def web_home(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse(url="/web/dashboard", status_code=303)

    return RedirectResponse(url="/web/login", status_code=303)


@router.get("/login", response_class=HTMLResponse)
def web_login_page(request: Request):
    return templates.TemplateResponse(
        "web_login.html",
        {
            "request": request,
            "error": None,
        },
    )


@router.post("/login", response_class=HTMLResponse)
def web_login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()

    identity_col = user_email_column()
    password_col = user_password_column()

    user = db.query(User).filter(
        getattr(User, identity_col) == email,
    ).first()

    if not user:
        return templates.TemplateResponse(
            "web_login.html",
            {
                "request": request,
                "error": "Invalid email or password",
                "form": {"email": email},
            },
            status_code=400,
        )

    stored_hash = getattr(user, password_col)

    if not verify_password(password, stored_hash):
        return templates.TemplateResponse(
            "web_login.html",
            {
                "request": request,
                "error": "Invalid email or password",
                "form": {"email": email},
            },
            status_code=400,
        )

    if hasattr(user, "is_active") and user.is_active is False:
        return templates.TemplateResponse(
            "web_login.html",
            {
                "request": request,
                "error": "User is inactive",
                "form": {"email": email},
            },
            status_code=400,
        )

    if hasattr(user, "last_login"):
        user.last_login = datetime.now(timezone.utc)
        db.commit()

    role_value = normalized_role(user)

    request.session.clear()
    request.session["user_id"] = user.id
    request.session["role"] = role_value

    if hasattr(user, "company_id"):
        request.session["company_id"] = user.company_id

    if role_value == "owner":
        return RedirectResponse(url="/admin/sip-numbers", status_code=303)

    return RedirectResponse(url="/web/dashboard", status_code=303)


@router.get("/register", response_class=HTMLResponse)
def web_register_page(request: Request):
    return templates.TemplateResponse(
        "web_register.html",
        {
            "request": request,
            "error": None,
        },
    )


@router.post("/register", response_class=HTMLResponse)
def web_register(
    request: Request,
    company_name: str = Form(...),
    full_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(""),
    db: Session = Depends(get_db),
):
    company_name = company_name.strip()
    full_name = full_name.strip()
    email = email.strip().lower()

    # Defaults to "" rather than being required so an older cached copy of the
    # form (or a client that omits the field) fails closed with a clear message
    # instead of a 422.
    if password != confirm_password:
        return templates.TemplateResponse(
            "web_register.html",
            {
                "request": request,
                "error": "Passwords do not match",
                "form": {
                    "company_name": company_name,
                    "full_name": full_name,
                    "email": email,
                },
            },
            status_code=400,
        )

    if len(password) < 6:
        return templates.TemplateResponse(
            "web_register.html",
            {
                "request": request,
                "error": "Password must be at least 6 characters",
                "form": {
                    "company_name": company_name,
                    "full_name": full_name,
                    "email": email,
                },
            },
            status_code=400,
        )


    if not is_bcrypt_safe_password(password):
        return templates.TemplateResponse(
            "web_register.html",
            {
                "request": request,
                "error": "Password is too long. Please use 72 bytes or less.",
                "form": {
                    "company_name": company_name,
                    "full_name": full_name,
                    "email": email,
                },
            },
            status_code=400,
        )

    identity_col = user_email_column()
    password_col = user_password_column()

    existing_user = db.query(User).filter(
        getattr(User, identity_col) == email,
    ).first()

    if existing_user:
        return templates.TemplateResponse(
            "web_register.html",
            {
                "request": request,
                "error": "Email already registered",
                "form": {
                    "company_name": company_name,
                    "full_name": full_name,
                    "email": email,
                },
            },
            status_code=400,
        )

    company_cols = model_columns(Company)
    company_kwargs = {}

    if "name" in company_cols:
        company_kwargs["name"] = company_name

    if "company_name" in company_cols:
        company_kwargs["company_name"] = company_name

    if "email" in company_cols:
        company_kwargs["email"] = email

    if "phone" in company_cols:
        company_kwargs["phone"] = ""

    if "plan" in company_cols:
        company_kwargs["plan"] = "starter"

    if "is_active" in company_cols:
        company_kwargs["is_active"] = True

    if "max_contacts" in company_cols:
        company_kwargs["max_contacts"] = 100000

    if "max_campaigns" in company_cols:
        company_kwargs["max_campaigns"] = 1000

    user_cols = model_columns(User)
    user_kwargs = {}

    try:
        company = Company(**company_kwargs)
        db.add(company)
        db.flush()

        if "company_id" in user_cols:
            user_kwargs["company_id"] = company.id

        if "email" in user_cols:
            user_kwargs["email"] = email

        if "username" in user_cols:
            user_kwargs["username"] = email

        if "full_name" in user_cols:
            user_kwargs["full_name"] = full_name

        if "name" in user_cols:
            user_kwargs["name"] = full_name

        if "role" in user_cols:
            # Self-service signup creates the admin of a brand new company, not
            # a platform owner. "owner" is what get_current_super_admin() in
            # app/routes/admin_routes.py checks for, and that unlocks the
            # global SIP trunk pages - every tenant's trunks, plus rewriting
            # and reloading Asterisk's PJSIP config. Granting it here made the
            # public register form a super-admin signup.
            user_kwargs["role"] = "admin"

        if "is_active" in user_cols:
            user_kwargs["is_active"] = True

        user_kwargs[password_col] = hash_password(password)

        user = User(**user_kwargs)
        db.add(user)
        db.commit()
        db.refresh(user)

    except IntegrityError:
        db.rollback()
        return templates.TemplateResponse(
            "web_register.html",
            {
                "request": request,
                "error": "Company or email already exists",
                "form": {
                    "company_name": company_name,
                    "full_name": full_name,
                    "email": email,
                },
            },
            status_code=400,
        )

    except Exception as e:
        db.rollback()
        return templates.TemplateResponse(
            "web_register.html",
            {
                "request": request,
                "error": str(e),
            },
            status_code=500,
        )

    role_value = normalized_role(user)

    request.session.clear()
    request.session["user_id"] = user.id
    request.session["role"] = role_value

    if hasattr(user, "company_id"):
        request.session["company_id"] = user.company_id

    if role_value == "owner":
        return RedirectResponse(url="/admin/sip-numbers", status_code=303)

    return RedirectResponse(url="/web/dashboard", status_code=303)


@router.post("/logout")
def web_logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/web/login", status_code=303)


@router.get("/logout")
def web_logout_get(request: Request):
    request.session.clear()
    return RedirectResponse(url="/web/login", status_code=303)


RESET_CODE_EXPIRY_MINUTES = 10
RESET_CODE_MAX_ATTEMPTS = 5


def hash_reset_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def generate_reset_code() -> str:
    # 6 digits, zero-padded (e.g. "004821"), not a link. Matches the code
    # entropy of standard OTP flows (Facebook, Google, etc): short by design,
    # so it relies on RESET_CODE_EXPIRY_MINUTES + RESET_CODE_MAX_ATTEMPTS for
    # safety rather than raw randomness the way the old 32-byte token did.
    return f"{secrets.randbelow(1_000_000):06d}"


@router.get("/forgot-password", response_class=HTMLResponse)
def web_forgot_password_page(request: Request):
    return templates.TemplateResponse(
        "web_forgot_password.html",
        {"request": request, "message": None, "error": None, "email": ""},
    )


@router.post("/forgot-password", response_class=HTMLResponse)
def web_forgot_password(
    request: Request,
    email: str = Form(...),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()
    identity_col = user_email_column()

    user = db.query(User).filter(getattr(User, identity_col) == email).first()

    # Always show the same message, whether or not the email exists.
    # Otherwise this endpoint becomes a way to check which emails are registered.
    generic_message = (
        f"If that email is registered, a 6-digit code has been sent. "
        f"It expires in {RESET_CODE_EXPIRY_MINUTES} minutes."
    )

    if user:
        code = generate_reset_code()
        user.reset_token_hash = hash_reset_code(code)
        user.reset_token_expires_at = datetime.now(timezone.utc) + timedelta(
            minutes=RESET_CODE_EXPIRY_MINUTES
        )
        user.reset_attempts = 0
        db.commit()

        sent = send_password_reset_email(
            email,
            code,
            expiry_minutes=RESET_CODE_EXPIRY_MINUTES,
            language=request_language(request),
        )

        # Temporary bridge while the SMTP provider isn't activated yet. Logs
        # server-side only (journalctl), never in the HTTP response - showing
        # it on-page would let anyone who knows a user's email pull their
        # reset code with no access to that inbox at all. Only someone with
        # server access can read a systemd journal.
        # See DEV_SHOW_RESET_CODE_ON_SEND_FAILURE docstring in config.py.
        if not sent and settings.DEV_SHOW_RESET_CODE_ON_SEND_FAILURE:
            print(f"DEV RESET CODE (email send failed): {email} -> {code}")

    return templates.TemplateResponse(
        "web_forgot_password.html",
        {"request": request, "message": generic_message, "error": None, "email": email},
    )


@router.get("/reset-password", response_class=HTMLResponse)
def web_reset_password_page(request: Request, email: str = ""):
    return templates.TemplateResponse(
        "web_reset_password.html",
        {"request": request, "email": email, "error": None},
    )


@router.post("/reset-password", response_class=HTMLResponse)
def web_reset_password(
    request: Request,
    email: str = Form(...),
    code: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()
    code = code.strip()

    def error_page(message: str, status_code: int = 400):
        return templates.TemplateResponse(
            "web_reset_password.html",
            {"request": request, "email": email, "error": message},
            status_code=status_code,
        )

    if password != confirm_password:
        return error_page("Passwords do not match.")

    if len(password) < 6:
        return error_page("Password must be at least 6 characters.")

    if not is_bcrypt_safe_password(password):
        return error_page("Password is too long. Please use 72 bytes or less.")

    identity_col = user_email_column()
    user = db.query(User).filter(getattr(User, identity_col) == email).first()

    # Same generic error whether the email doesn't exist, the code is wrong,
    # or it expired - do not reveal which, same reasoning as forgot-password.
    generic_error = "Invalid or expired code. Request a new one."

    if not user or not user.reset_token_hash or not user.reset_token_expires_at:
        return error_page(generic_error)

    if user.reset_token_expires_at <= datetime.now(timezone.utc):
        return error_page(generic_error)

    if (user.reset_attempts or 0) >= RESET_CODE_MAX_ATTEMPTS:
        return error_page("Too many incorrect attempts. Request a new code.")

    if not secrets.compare_digest(hash_reset_code(code), user.reset_token_hash):
        user.reset_attempts = (user.reset_attempts or 0) + 1
        db.commit()
        return error_page(generic_error)

    password_col = user_password_column()
    setattr(user, password_col, hash_password(password))

    # Single-use: clear it so the same code can't be replayed.
    user.reset_token_hash = None
    user.reset_token_expires_at = None
    user.reset_attempts = 0
    db.commit()

    return RedirectResponse(url="/web/login", status_code=303)