from typing import List, Optional, Union
from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.database import get_db
from app.core.network import get_lan_ip, resolve_effective_base_url
from app.schemas.item import ItemRead, VisionAnalysisResponse
from app.schemas.location import (
    GridSlotEnsureRequest,
    GridSlotMoveRequest,
    LocationCreate,
    LocationGridSave,
    LocationPhotoAttach,
    LocationPhotoRead,
    LocationPhotoType,
    LocationRead,
    LocationTree,
    LocationUpdate,
)
from app.schemas.system import FindLocationItemImagesResponse, NetworkInfoResponse
from app.services.image_lookup_service import find_and_import_images_batch, import_images_for_location
from app.services.inventory_service import inventory_service
from app.services.location_service import location_service

router = APIRouter(prefix="/locations", tags=["Locations"])


@router.post("", response_model=LocationRead, status_code=status.HTTP_201_CREATED)
def create_location(data: LocationCreate, db: Session = Depends(get_db)) -> LocationRead:
    loc = location_service.create(db, data)
    return location_service.get_read_by_id(db, loc.id)


@router.get("", response_model=Union[List[LocationTree], List[LocationRead]])
def list_locations(
    tree: bool = Query(default=False, description="Return nested hierarchy tree"),
    db: Session = Depends(get_db),
) -> Union[List[LocationTree], List[LocationRead]]:
    if tree:
        return location_service.get_tree(db)
    return location_service.get_all(db)


@router.get("/tree", response_model=List[LocationTree])
def get_locations_tree(db: Session = Depends(get_db)) -> List[LocationTree]:
    return location_service.get_tree(db)


@router.get("/network-info", response_model=NetworkInfoResponse)
def get_network_info(request: Request) -> NetworkInfoResponse:
    host = request.headers.get("host")
    return NetworkInfoResponse(
        lan_ip=get_lan_ip(),
        port=settings.PORT,
        base_url=resolve_effective_base_url(host),
        request_host=host,
    )


@router.get("/{location_id}/qr")
def get_location_qr(
    location_id: int,
    request: Request,
    target_url: Optional[str] = Query(default=None, max_length=500),
    base_url: Optional[str] = Query(default=None, max_length=300),
    db: Session = Depends(get_db),
) -> Response:
    png_bytes = location_service.generate_qr_png(
        db=db,
        location_id=location_id,
        host_header=request.headers.get("host"),
        target_url=target_url,
        base_url=base_url,
    )
    return Response(content=png_bytes, media_type="image/png")


@router.get("/{location_id}/photos", response_model=List[LocationPhotoRead])
def list_location_photos(location_id: int, db: Session = Depends(get_db)) -> List[LocationPhotoRead]:
    return [LocationPhotoRead.model_validate(p) for p in location_service.list_photos(db, location_id)]


@router.post("/{location_id}/photos", response_model=LocationPhotoRead, status_code=status.HTTP_201_CREATED)
def upload_location_photo(
    location_id: int,
    file: UploadFile = File(...),
    caption: Optional[str] = Form(default=None),
    photo_type: LocationPhotoType = Form(default="container"),
    db: Session = Depends(get_db),
) -> LocationPhotoRead:
    photo = location_service.add_photo(
        db=db,
        location_id=location_id,
        file=file,
        caption=caption,
        photo_type=photo_type,
    )
    return LocationPhotoRead.model_validate(photo)


@router.post("/{location_id}/photos/attach", response_model=LocationPhotoRead, status_code=status.HTTP_201_CREATED)
def attach_location_photo(
    location_id: int,
    data: LocationPhotoAttach,
    db: Session = Depends(get_db),
) -> LocationPhotoRead:
    photo = location_service.attach_photo(
        db=db,
        location_id=location_id,
        photo_path=data.photo_path,
        thumbnail_path=data.thumbnail_path,
        caption=data.caption,
        photo_type=data.photo_type,
    )
    return LocationPhotoRead.model_validate(photo)


@router.delete("/{location_id}/photos/{photo_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_location_photo(location_id: int, photo_id: int, db: Session = Depends(get_db)) -> None:
    location_service.delete_photo(db, location_id, photo_id)
    return None


@router.post("/{location_id}/analyze-photo", response_model=VisionAnalysisResponse)
async def analyze_drawer_photo(
    location_id: int,
    file: Optional[UploadFile] = File(default=None),
    photo_id: Optional[int] = Form(default=None),
    photo_ids: Optional[str] = Form(default=None, max_length=200),
    provider: Optional[str] = Form(default=None),
    model: Optional[str] = Form(default=None),
    expected_count: Optional[int] = Form(default=None, ge=1, le=500),
    context_hint: Optional[str] = Form(default=None, max_length=300),
    db: Session = Depends(get_db),
) -> VisionAnalysisResponse:
    return await location_service.analyze_drawer_photo(
        db=db,
        location_id=location_id,
        file=file,
        photo_id=photo_id,
        photo_ids=photo_ids,
        provider=provider,
        model=model,
        expected_count=expected_count,
        context_hint=context_hint,
    )


@router.post("/{location_id}/find-item-images", response_model=FindLocationItemImagesResponse)
async def find_location_item_images(
    location_id: int,
    force: bool = Query(default=False),
    include_crops: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> FindLocationItemImagesResponse:
    return await import_images_for_location(
        db.get_bind(),
        location_id,
        force=force,
        include_crops=include_crops,
        batch_lookup_fn=find_and_import_images_batch,
    )


@router.get("/{location_id}", response_model=LocationRead)
def get_location(location_id: int, db: Session = Depends(get_db)) -> LocationRead:
    return location_service.get_read_by_id(db, location_id)


@router.get("/{location_id}/items", response_model=List[ItemRead])
def get_location_items(location_id: int, db: Session = Depends(get_db)) -> List[ItemRead]:
    return inventory_service.list_items_for_location(db, location_id)


@router.post("/{location_id}/grid/slot", response_model=LocationRead)
def ensure_location_grid_slot(
    location_id: int,
    data: GridSlotEnsureRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> LocationRead:
    loc, created = location_service.ensure_grid_slot(db, location_id, data)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return location_service.get_read_by_id(db, loc.id)


@router.put("/{location_id}/grid", response_model=LocationTree)
def save_location_grid(location_id: int, data: LocationGridSave, db: Session = Depends(get_db)) -> LocationTree:
    return location_service.save_grid_layout(db, location_id, data)


@router.post("/{location_id}/grid/move", response_model=LocationTree)
def move_location_grid_slot(
    location_id: int,
    data: GridSlotMoveRequest,
    db: Session = Depends(get_db),
) -> LocationTree:
    return location_service.move_grid_slot(db, location_id, data)


@router.patch("/{location_id}", response_model=LocationRead)
@router.put("/{location_id}", response_model=LocationRead)
def update_location(location_id: int, data: LocationUpdate, db: Session = Depends(get_db)) -> LocationRead:
    loc = location_service.update(db, location_id, data)
    return location_service.get_read_by_id(db, loc.id)


@router.delete("/{location_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_location(location_id: int, db: Session = Depends(get_db)) -> None:
    location_service.delete(db, location_id)
    return None
