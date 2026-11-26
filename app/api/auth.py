from fastapi import APIRouter, Request, Response, status
from starlette.datastructures import Headers
from app.core.config import settings
from app.core.exceptions import AppError
from app.core.security import (
    clear_session_cookie,
    is_auth_enabled,
    is_request_authenticated,
    set_session_cookie,
    verify_pin,
)
from app.schemas.auth import AuthLoginRequest, AuthStatusResponse

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.get("/status", response_model=AuthStatusResponse)
def get_auth_status(request: Request) -> AuthStatusResponse:
    client_host = request.client.host if request.client else None
    authenticated = is_request_authenticated(Headers(scope=request.scope), client_host)
    return AuthStatusResponse(
        auth_enabled=is_auth_enabled(),
        authenticated=authenticated,
        localhost_bypass=settings.WIMS_AUTH_BYPASS_LOCALHOST,
    )


@router.post("/login", response_model=AuthStatusResponse)
def login_with_pin(
    payload: AuthLoginRequest,
    request: Request,
    response: Response,
) -> AuthStatusResponse:
    if not is_auth_enabled():
        return AuthStatusResponse(
            auth_enabled=False,
            authenticated=True,
            localhost_bypass=settings.WIMS_AUTH_BYPASS_LOCALHOST,
        )
    if not verify_pin(payload.pin):
        raise AppError(
            "Invalid access PIN.",
            status_code=status.HTTP_401_UNAUTHORIZED,
            error_code="invalid_credentials",
        )
    set_session_cookie(response, is_https=(request.url.scheme == "https"))
    return AuthStatusResponse(
        auth_enabled=True,
        authenticated=True,
        localhost_bypass=settings.WIMS_AUTH_BYPASS_LOCALHOST,
    )


@router.post("/logout", response_model=AuthStatusResponse)
def logout(request: Request, response: Response) -> AuthStatusResponse:
    clear_session_cookie(response)
    client_host = request.client.host if request.client else None
    authenticated = not is_auth_enabled() or (
        settings.WIMS_AUTH_BYPASS_LOCALHOST and client_host in ("127.0.0.1", "::1", "localhost")
    )
    return AuthStatusResponse(
        auth_enabled=is_auth_enabled(),
        authenticated=authenticated,
        localhost_bypass=settings.WIMS_AUTH_BYPASS_LOCALHOST,
    )
