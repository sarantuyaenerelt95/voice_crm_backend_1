# app/routes/web_auth_routes.py

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from passlib.context import CryptContext

from app.database import get_db
from app.models.user import User
from app.models.company import Company


router = APIRouter(prefix="/web", tags=["web-auth"])
templates = Jinja2Templates(directory="app/templates")

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
            },
            status_code=400,
        )

    if hasattr(user, "is_active") and user.is_active is False:
        return templates.TemplateResponse(
            "web_login.html",
            {
                "request": request,
                "error": "User is inactive",
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
    db: Session = Depends(get_db),
):
    company_name = company_name.strip()
    full_name = full_name.strip()
    email = email.strip().lower()

    if len(password) < 6:
        return templates.TemplateResponse(
            "web_register.html",
            {
                "request": request,
                "error": "Password must be at least 6 characters",
            },
            status_code=400,
        )


    if not is_bcrypt_safe_password(password):
        return templates.TemplateResponse(
            "web_register.html",
            {
                "request": request,
                "error": "Password is too long. Please use 72 bytes or less.",
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
        company_kwargs["max_contacts"] = 30000

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
            user_kwargs["role"] = "owner"

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