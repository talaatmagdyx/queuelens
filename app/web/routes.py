from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth.basic import get_current_username


def _identity_context(request: Request) -> dict[str, Any]:
    """Expose the acting Basic Auth username to the error template."""
    import base64

    header = request.headers.get("authorization", "")
    if header.lower().startswith("basic "):
        try:
            decoded = base64.b64decode(header.split(" ", 1)[1]).decode("utf-8", "replace")
            return {"current_user": decoded.split(":", 1)[0]}
        except Exception:
            pass
    return {"current_user": None}


router = APIRouter(tags=["web"])
# Kept only for error.html — the console itself is the SPA at /app.
templates = Jinja2Templates(
    directory=str(Path(__file__).parent / "templates"),
    context_processors=[_identity_context],
)


@router.get("/app")
async def spa(
    _username: str = Depends(get_current_username),
) -> FileResponse:
    """The console, served as a single-page app wired to the live API.
    Served directly (asset URLs are absolute) so the address bar stays /app;
    auth here forces the Basic Auth prompt before the SPA loads."""
    index = Path(__file__).parent / "static" / "ds" / "ui_kits" / "queuelens" / "index.html"
    return FileResponse(index, media_type="text/html")


@router.get("/")
async def root(
    _username: str = Depends(get_current_username),
) -> RedirectResponse:
    return RedirectResponse(url="/app")


# The server-rendered console was retired once the SPA covered everything it
# did (and more). Old bookmarks and inbound links land on the SPA instead.
@router.get("/login")
@router.get("/classic")
@router.get("/queues")
@router.get("/queues/{rest:path}")
@router.get("/messages")
@router.get("/messages/{rest:path}")
@router.get("/replay")
@router.get("/users")
@router.get("/notifications")
@router.get("/audit")
@router.get("/config")
async def legacy_console(rest: str = "") -> RedirectResponse:
    return RedirectResponse(url="/app", status_code=301)
