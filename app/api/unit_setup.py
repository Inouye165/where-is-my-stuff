from fastapi import APIRouter, Body, Depends, Query, Request, Response, status
from sqlalchemy.orm import Session
from typing import Optional
from app.core.database import get_db
from app.core.exceptions import NotFoundError
from app.core.security import is_auth_enabled, set_session_cookie
from app.schemas.unit_setup import (
    UnitSetupActiveSessionResponse,
    UnitSetupCreateUnitRequest,
    UnitSetupProgressUpdate,
    UnitSetupSessionCreate,
    UnitSetupSessionCreateResponse,
    UnitSetupSessionStatusResponse,
)
from app.services.unit_setup_service import unit_setup_service

router = APIRouter(prefix="/unit-setup", tags=["Unit Setup"])


@router.post(
    "/sessions",
    response_model=UnitSetupSessionCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_unit_setup_session(
    request: Request,
    payload: Optional[UnitSetupSessionCreate] = Body(default=None),
    db: Session = Depends(get_db),
) -> UnitSetupSessionCreateResponse:
    data = unit_setup_service.create_session(
        db=db,
        room_id=payload.room_id if payload else None,
        unit_id=payload.unit_id if payload else None,
        host_header=request.headers.get("host"),
        base_url_param=payload.base_url if payload else None,
    )
    return UnitSetupSessionCreateResponse(**data)


@router.get("/sessions/active", response_model=UnitSetupActiveSessionResponse)
@router.get("/active", response_model=UnitSetupActiveSessionResponse)
def get_active_unit_setup_session(
    request: Request,
    db: Session = Depends(get_db),
) -> UnitSetupActiveSessionResponse:
    active = unit_setup_service.get_latest_active_session(
        db=db,
        host_header=request.headers.get("host"),
    )
    if not active:
        raise NotFoundError("No active phone capture session found.")
    return UnitSetupActiveSessionResponse(**active)


@router.get("/sessions/{token}", response_model=UnitSetupSessionStatusResponse)
def get_unit_setup_session(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
) -> UnitSetupSessionStatusResponse:
    return unit_setup_service.get_session_status(
        db=db,
        token=token,
        host_header=request.headers.get("host"),
    )


@router.get("/sessions/{token}/qr")
def get_unit_setup_qr(
    token: str,
    request: Request,
    base_url: Optional[str] = Query(default=None, max_length=300),
    db: Session = Depends(get_db),
) -> Response:
    png_bytes = unit_setup_service.generate_qr_png(
        db=db,
        token=token,
        host_header=request.headers.get("host"),
        base_url_param=base_url,
    )
    return Response(content=png_bytes, media_type="image/png")


@router.post("/sessions/{token}/connect", response_model=UnitSetupSessionStatusResponse)
def connect_unit_setup_phone(
    token: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> UnitSetupSessionStatusResponse:
    status_resp = unit_setup_service.connect_phone(
        db=db,
        token=token,
        host_header=request.headers.get("host"),
    )
    if is_auth_enabled():
        set_session_cookie(
            response,
            is_https=(request.url.scheme == "https"),
            ttl_seconds=max(300, status_resp.expires_in_seconds),
        )
    return status_resp


@router.post(
    "/sessions/{token}/unit",
    response_model=UnitSetupSessionStatusResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_unit_setup_unit(
    token: str,
    data: UnitSetupCreateUnitRequest,
    db: Session = Depends(get_db),
) -> UnitSetupSessionStatusResponse:
    return unit_setup_service.create_unit_for_session(db=db, token=token, data=data)


@router.patch("/sessions/{token}/progress", response_model=UnitSetupSessionStatusResponse)
def update_unit_setup_progress(
    token: str,
    data: UnitSetupProgressUpdate,
    db: Session = Depends(get_db),
) -> UnitSetupSessionStatusResponse:
    return unit_setup_service.update_progress(db=db, token=token, data=data)
