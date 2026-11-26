from io import BytesIO
from PIL import Image
import pytest

from app.schemas.unit_setup import UnitSetupCreateUnitRequest
from app.services.capture_session_service import CaptureSessionService
from app.services.unit_setup_service import UnitSetupService


def _make_jpeg_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (64, 64), color=(20, 120, 200)).save(buf, format="JPEG")
    return buf.getvalue()


def test_capture_session_persists_across_service_instances(client, db_session):
    worker_a = CaptureSessionService()
    worker_b = CaptureSessionService()

    created = worker_a.create_session(db_session, label="Multi-Worker Bin")
    token = created["token"]
    db_session.commit()

    # Worker B (separate instance, simulating another Uvicorn worker) reads & updates the session
    snap = worker_b.get_session(db_session, token)
    assert snap.label == "Multi-Worker Bin"
    assert snap.status == "pending"

    updated = worker_b.mark_uploaded(
        db_session,
        token,
        {"original_path": "/uploads/originals/a.jpg", "thumbnail_path": "/uploads/thumbnails/a_thumb.webp", "filename": "a.jpg"},
    )
    db_session.commit()
    assert updated.status == "uploaded"
    assert len(updated.photos) == 1

    # Worker A sees the uploaded photo
    assert len(worker_a.get_session(db_session, token).photos) == 1


def test_unit_setup_session_persists_and_reserves_atomically_across_instances(db_session):
    from app.core.exceptions import BadRequestError

    worker_a = UnitSetupService()
    worker_b = UnitSetupService()

    created = worker_a.create_session(db_session)
    token = created["token"]
    db_session.commit()

    connected = worker_b.connect_phone(db_session, token)
    assert connected.status == "creating_unit"

    created_resp = worker_b.create_unit_for_session(
        db_session,
        token,
        UnitSetupCreateUnitRequest(name="Shared Cabinet", compartment_count=2, grid_cols=1),
    )
    assert created_resp.status == "capturing_drawers"
    assert created_resp.unit_id is not None

    # Worker A cannot create a second unit for the same session
    with pytest.raises(BadRequestError):
        worker_a.create_unit_for_session(
            db_session,
            token,
            UnitSetupCreateUnitRequest(name="Duplicate Cabinet", compartment_count=1, grid_cols=1),
        )
