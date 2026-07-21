# app/main.py
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.routes import auth_routes, campaign_routes, web_routes, web_auth_routes, admin_routes
STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="Voice CRM Backend",
    description="Multi-tenant Voice Broadcast API",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
)
templates = Jinja2Templates(directory="app/templates")





app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


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

@app.middleware("http")
async def require_web_login(request, call_next):
    path = request.url.path

    public_paths = {
        "/web/login",
        "/web/register",
        "/web/logout",
    }

    if path.startswith("/web") and path not in public_paths:
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
    secret_key=os.getenv("SESSION_SECRET_KEY", "change-this-secret-key-in-production"),
    same_site="lax",
    https_only=False,
)

# Register routes
app.include_router(auth_routes.router)
app.include_router(campaign_routes.router)
app.include_router(web_auth_routes.router)
app.include_router(web_routes.router)
app.include_router(admin_routes.router)


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "Voice CRM API"}
