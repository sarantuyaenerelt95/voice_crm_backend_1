# app/main.py

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.routes import (
    auth_routes,
    campaign_routes,
    web_routes,
    web_auth_routes,
    admin_routes,
)




BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"


app = FastAPI(
    title="Voice CRM Backend",
    description="Multi-tenant Voice Broadcast API",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
)

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))



if STATIC_DIR.exists():
    app.mount(
        "/static",
        StaticFiles(directory=str(STATIC_DIR)),
        name="static",
    )


@app.middleware("http")
async def require_web_login(request: Request, call_next):
    path = request.url.path

    public_paths = {
        "/",
        "/home",
        "/health",
        "/docs",
        "/openapi.json",
        "/web",
        "/web/",
        "/web/login",
        "/web/register",
        "/web/logout",
    }

    if (
        path in public_paths
        or path.startswith("/static")
        or path.startswith("/auth")
        or path.startswith("/campaigns")
    ):
        return await call_next(request)

    if path.startswith("/web"):
        if not request.session.get("user_id"):
            return RedirectResponse(url="/web/login", status_code=303)

        role_value = str(request.session.get("role", "")).lower().strip()

        if "." in role_value:
            role_value = role_value.split(".")[-1]

        if role_value == "owner":
            return RedirectResponse(url="/admin/sip-numbers", status_code=303)

    if path.startswith("/admin"):
        if not request.session.get("user_id"):
            return RedirectResponse(url="/web/login", status_code=303)

    return await call_next(request)


app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET_KEY", settings.SECRET_KEY),
    same_site="lax",
    https_only=False,
)


@app.get("/docs", include_in_schema=False)
def custom_swagger_ui_html():
    return HTMLResponse(
        """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Voice CRM Backend - Swagger UI</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
  <style>
    body { margin: 0; font-family: Arial, sans-serif; background: #f7f7f7; }
    #docs-fallback { padding: 24px; color: #222; }
    #docs-fallback h1 { margin: 0 0 12px; font-size: 24px; }
    #docs-fallback code { background: #eee; padding: 2px 6px; border-radius: 4px; }
    #docs-fallback a { color: #0f62fe; }
  </style>
</head>
<body>
  <div id="docs-fallback">
    <h1>Voice CRM Backend API</h1>
    <p>Swagger UI is loading. If it does not appear, these API links are working:</p>
    <p><a href="/openapi.json">/openapi.json</a> · <a href="/health">/health</a></p>
    <p>CSV/TXT contact import endpoint: <code>POST /campaigns/contacts/import</code></p>
  </div>
  <div id="swagger-ui"></div>
  <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
  <script>
    try {
      if (window.SwaggerUIBundle) {
        document.getElementById('docs-fallback').style.display = 'none';
        window.SwaggerUIBundle({
          url: '/openapi.json',
          dom_id: '#swagger-ui',
          deepLinking: true,
          presets: [
            window.SwaggerUIBundle.presets.apis,
            window.SwaggerUIBundle.SwaggerUIStandalonePreset
          ],
          layout: 'BaseLayout',
          showExtensions: true,
          showCommonExtensions: true
        });
      }
    } catch (error) {
      document.getElementById('docs-fallback').style.display = 'block';
      console.error('Swagger UI failed to start', error);
    }
  </script>
</body>
</html>
        """
    )


@app.get("/", response_class=HTMLResponse)
def public_home(request: Request):
    return templates.TemplateResponse(
        "home.html",
        {
            "request": request,
        },
    )


@app.get("/home", response_class=HTMLResponse)
def public_home_alias(request: Request):
    return templates.TemplateResponse(
        "home.html",
        {
            "request": request,
        },
    )


ERROR_TITLES = {
    400: "Something was wrong with that request",
    401: "Please sign in",
    402: "Not enough call tokens",
    403: "You do not have access to this",
    404: "Page not found",
    409: "That action conflicts with the current state",
    500: "Something went wrong on our side",
}


@app.exception_handler(StarletteHTTPException)
async def html_error_handler(request: Request, exc: StarletteHTTPException):
    """Render browser errors as a page instead of a raw JSON blob.

    API clients under /auth, /campaigns and /docs still get JSON.
    """
    path = request.url.path
    is_browser_page = path.startswith("/web") or path.startswith("/admin") or path == "/"

    if not is_browser_page:
        return await http_exception_handler(request, exc)

    detail = exc.detail

    if isinstance(detail, dict):
        detail = detail.get("message") or "Unexpected error."

    return templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "status_code": exc.status_code,
            "title": ERROR_TITLES.get(exc.status_code, "Error"),
            "detail": detail,
            "user": request.session.get("user_id") if hasattr(request, "session") else None,
        },
        status_code=exc.status_code,
    )


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": "Voice CRM API",
    }


app.include_router(auth_routes.router)
app.include_router(campaign_routes.router)
app.include_router(web_auth_routes.router)
app.include_router(web_routes.router)
app.include_router(admin_routes.router)