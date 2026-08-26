# app/i18n/templating.py

"""Wire the language into Jinja.

Every template gets `t`, `lang` and `languages` without any route having to
pass them, so translating a page never means touching its handler. The
language is resolved once per request by the middleware and stashed on
request.state.
"""

from __future__ import annotations

from starlette.requests import Request

from app import i18n


def request_language(request: Request) -> str:
    """The language chosen for this request, resolving late if needed.

    Normally the middleware has already decided and stored it. The fallback
    keeps templates rendering if something bypasses the middleware, such as an
    error page raised very early in the stack.
    """
    language = getattr(request.state, "language", None)

    if language:
        return language

    return i18n.resolve_language(
        cookie=request.cookies.get(i18n.LANGUAGE_COOKIE),
        accept_header=request.headers.get("accept-language"),
    )


def language_context(request: Request) -> dict:
    """The i18n names every template can rely on."""
    language = request_language(request)

    return {
        "t": i18n.translator(language),
        "lang": language,
        "languages": [
            {
                "code": code,
                "name": i18n.LANGUAGE_NAMES[code],
                "is_current": code == language,
            }
            for code in i18n.SUPPORTED_LANGUAGES
        ],
    }


def install(templates) -> None:
    """Make the i18n names available to every TemplateResponse.

    Starlette calls each context processor with the request, so this covers
    templates rendered anywhere in the app - including the ones in main.py that
    never see a route's context dict.
    """
    templates.env.globals.setdefault("t", lambda text, **kw: text)
    templates.env.globals.setdefault("lang", i18n.DEFAULT_LANGUAGE)

    processors = getattr(templates, "context_processors", None)

    if processors is None:
        # Older Starlette: fall back to patching the context at render time.
        original = templates.TemplateResponse

        def TemplateResponse(name, context, *args, **kwargs):
            request = context.get("request")

            if request is not None:
                for key, value in language_context(request).items():
                    context.setdefault(key, value)

            return original(name, context, *args, **kwargs)

        templates.TemplateResponse = TemplateResponse
        return

    processors.append(language_context)
