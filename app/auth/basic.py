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
    matched = False
    for username, password in settings.users.items():
        # compare every account to keep timing independent of username validity
        if compare_digest(credentials.username, username) and compare_digest(
            credentials.password, password
        ):
            matched = True
    if not matched:
        raise _unauthorized()
    return credentials.username


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Basic"},
    )

