from hmac import compare_digest

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

security = HTTPBasic(auto_error=False)


async def get_current_username(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(security),
) -> str:
    settings = request.app.state.settings
    if not settings.auth_enabled:
        return "local"
    if credentials is None:
        raise _unauthorized()
    if not (
        compare_digest(credentials.username, settings.admin_username)
        and compare_digest(credentials.password, settings.admin_password)
    ):
        raise _unauthorized()
    return credentials.username


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Basic"},
    )

