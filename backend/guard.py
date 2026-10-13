"""Only this app's own pages may talk to the backend from a browser.

The runner allows every origin (CORS "*"), so any web page open on this machine, including one the
errand browser visits, could otherwise open a voice session and answer its own permission question.
The orb's page is a file:// page (Origin "null"), the /client playground is served from this server,
and the claude CLI's hooks send no Origin at all.
"""

from starlette.responses import PlainTextResponse


def refuse_other_origins(app, port):
    allowed = {"null", f"http://localhost:{port}", f"http://127.0.0.1:{port}"}

    @app.middleware("http")
    async def check_origin(request, call_next):
        origin = request.headers.get("origin")
        if origin is not None and origin not in allowed:
            return PlainTextResponse("forbidden origin", status_code=403)
        return await call_next(request)
