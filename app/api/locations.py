from typing import List, Optional, Union
from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile, status
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.database import get_db
from app.core.network import get_lan_ip, resolve_effective_base_url
from app.schemas.item import ItemRead, VisionAnalysisResponse
from app.schemas.location import LocationCreate, LocationGridSave, LocationRead, LocationTree, LocationUpdate
from app.schemas.system import NetworkInfoResponse
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


@router.post("/{location_id}/analyze-photo", response_model=VisionAnalysisResponse)
async def analyze_drawer_photo(
    location_id: int,
    file: Optional[UploadFile] = File(default=None),
    db: Session = Depends(get_db),
) -> VisionAnalysisResponse:
    return await location_service.analyze_drawer_photo(db=db, location_id=location_id, file=file)


@router.get("/{location_id}", response_model=LocationRead)
def get_location(location_id: int, db: Session = Depends(get_db)) -> LocationRead:
    return location_service.get_read_by_id(db, location_id)


@router.get("/{location_id}/items", response_model=List[ItemRead])
def get_location_items(location_id: int, db: Session = Depends(get_db)) -> List[ItemRead]:
    return inventory_service.list_items_for_location(db, location_id)


@router.put("/{location_id}/grid", response_model=LocationTree)
def save_location_grid(location_id: int, data: LocationGridSave, db: Session = Depends(get_db)) -> LocationTree:
    return location_service.save_grid_layout(db, location_id, data)


@router.patch("/{location_id}", response_model=LocationRead)
@router.put("/{location_id}", response_model=LocationRead)
def update_location(location_id: int, data: LocationUpdate, db: Session = Depends(get_db)) -> LocationRead:
    loc = location_service.update(db, location_id, data)
    return location_service.get_read_by_id(db, loc.id)


@router.delete("/{location_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_location(location_id: int, db: Session = Depends(get_db)) -> None:
    location_service.delete(db, location_id)
    return None
