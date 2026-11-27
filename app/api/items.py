from typing import List, Optional
from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.schemas.audit import AuditVerifyRequest
from app.schemas.common import ItemSortOrder, ItemStatus
from app.schemas.item import (
    FindItemImageResponse,
    ItemAttachmentRead,
    ItemBulkCreate,
    ItemBundleAnalysisResponse,
    ItemConvertToBoxRequest,
    ItemCreate,
    ItemDetailRead,
    ItemFromBundleCreate,
    ItemGroupIntoBoxRequest,
    ItemGroupIntoBoxResponse,
    ItemImagesToPdfRequest,
    ItemPageResponse,
    ItemPhotoAttachmentCreate,
    ItemRead,
    ItemUpdate,
)
from app.schemas.location import LocationRead
from app.services.image_lookup_service import import_image_for_item
from app.services.inventory_service import inventory_service
from app.services.item_bundle_service import item_bundle_service

router = APIRouter(prefix="/items", tags=["Items"])


@router.post("", response_model=ItemRead, status_code=status.HTTP_201_CREATED)
def create_item(data: ItemCreate, db: Session = Depends(get_db)) -> ItemRead:
    item = inventory_service.create(db, data)
    return inventory_service.to_read_schema(item, db=db)


@router.post("/bulk", response_model=List[ItemRead], status_code=status.HTTP_201_CREATED)
def create_items_bulk(data: ItemBulkCreate, db: Session = Depends(get_db)) -> List[ItemRead]:
    items = inventory_service.bulk_create(db, data)
    return inventory_service.serialize_many(db, items)


@router.post("/analyze-bundle", response_model=ItemBundleAnalysisResponse)
async def analyze_item_bundle(
    location_id: Optional[int] = Form(default=None),
    urls: Optional[str] = Form(default=None, max_length=10_000),
    pasted_text: Optional[str] = Form(default=None, max_length=50_000),
    photo_paths: Optional[List[str]] = Form(default=None),
    provider: Optional[str] = Form(default=None),
    model: Optional[str] = Form(default=None),
    files: Optional[List[UploadFile]] = File(default=None),
) -> ItemBundleAnalysisResponse:
    return await item_bundle_service.analyze_bundle(
        location_id=location_id,
        files=files,
        photo_paths=photo_paths,
        urls=urls,
        pasted_text=pasted_text,
        provider=provider,
        model=model,
    )


@router.post("/from-bundle", response_model=ItemDetailRead, status_code=status.HTTP_201_CREATED)
def create_item_from_bundle(
    data: ItemFromBundleCreate,
    db: Session = Depends(get_db),
) -> ItemDetailRead:
    return item_bundle_service.create_item_from_bundle(db, data)


@router.post("/group-into-box", response_model=ItemGroupIntoBoxResponse, status_code=status.HTTP_201_CREATED)
def group_items_into_box(data: ItemGroupIntoBoxRequest, db: Session = Depends(get_db)) -> ItemGroupIntoBoxResponse:
    return inventory_service.group_items_into_box(db, data)


@router.post("/{item_id}/convert-to-box", response_model=LocationRead, status_code=status.HTTP_201_CREATED)
def convert_item_to_box(
    item_id: int,
    data: Optional[ItemConvertToBoxRequest] = None,
    db: Session = Depends(get_db),
) -> LocationRead:
    return inventory_service.convert_item_to_box(db, item_id, data or ItemConvertToBoxRequest())


@router.get("", response_model=List[ItemRead])
def list_items(
    category: Optional[str] = Query(default=None, max_length=80, description="Filter by category"),
    location_id: Optional[int] = Query(default=None, ge=1, description="Filter by direct location ID"),
    item_status: Optional[ItemStatus] = Query(default=None, alias="status", description="Filter by status"),
    sort: ItemSortOrder = Query(default=ItemSortOrder.last_seen_desc, description="Whitelisted sort order"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> List[ItemRead]:
    status_val = item_status.value if item_status is not None else None
    return inventory_service.list_items(
        db,
        category=category,
        location_id=location_id,
        status=status_val,
        sort=sort,
        limit=limit,
        offset=offset,
    )


@router.get("/page", response_model=ItemPageResponse)
def list_items_page(
    category: Optional[str] = Query(default=None, max_length=80, description="Filter by category"),
    location_id: Optional[int] = Query(default=None, ge=1, description="Filter by direct location ID"),
    item_status: Optional[ItemStatus] = Query(default=None, alias="status", description="Filter by status"),
    sort: ItemSortOrder = Query(default=ItemSortOrder.last_seen_desc, description="Whitelisted sort order"),
    limit: int = Query(default=100, ge=1, le=200),
    cursor: Optional[str] = Query(default=None, max_length=256),
    db: Session = Depends(get_db),
) -> ItemPageResponse:
    status_val = item_status.value if item_status is not None else None
    return inventory_service.list_items_page(
        db,
        category=category,
        location_id=location_id,
        status=status_val,
        sort=sort,
        limit=limit,
        cursor=cursor,
    )


@router.get("/{item_id}", response_model=ItemDetailRead)
def get_item(item_id: int, db: Session = Depends(get_db)) -> ItemDetailRead:
    return inventory_service.get_detail(db, item_id)


@router.patch("/{item_id}", response_model=ItemRead)
@router.put("/{item_id}", response_model=ItemRead)
def update_item(item_id: int, data: ItemUpdate, db: Session = Depends(get_db)) -> ItemRead:
    item = inventory_service.update(db, item_id, data)
    return inventory_service.to_read_schema(item, db=db)


@router.post("/{item_id}/verify", response_model=ItemDetailRead)
def verify_item_presence(
    item_id: int,
    req: Optional[AuditVerifyRequest] = None,
    db: Session = Depends(get_db),
) -> ItemDetailRead:
    notes = req.notes if req else None
    new_loc = req.location_id if req else None
    item = inventory_service.verify_presence(db, item_id, notes=notes, new_location_id=new_loc)
    return inventory_service.to_detail_schema(item, db=db)


@router.get("/{item_id}/attachments", response_model=List[ItemAttachmentRead])
def list_item_attachments(item_id: int, db: Session = Depends(get_db)) -> List[ItemAttachmentRead]:
    return [ItemAttachmentRead.model_validate(a) for a in inventory_service.list_attachments(db, item_id)]


@router.post("/{item_id}/attachments", response_model=ItemAttachmentRead, status_code=status.HTTP_201_CREATED)
def upload_item_attachment(
    item_id: int,
    file: UploadFile = File(...),
    title: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
) -> ItemAttachmentRead:
    att = inventory_service.add_attachment(db=db, item_id=item_id, file=file, title=title)
    return ItemAttachmentRead.model_validate(att)


@router.post(
    "/{item_id}/attachments/from-photo",
    response_model=ItemAttachmentRead,
    status_code=status.HTTP_201_CREATED,
)
def attach_photo_to_item(
    item_id: int,
    data: ItemPhotoAttachmentCreate,
    db: Session = Depends(get_db),
) -> ItemAttachmentRead:
    att = inventory_service.add_photo_attachment(db=db, item_id=item_id, data=data)
    return ItemAttachmentRead.model_validate(att)


@router.post(
    "/{item_id}/attachments/from-photos-pdf",
    response_model=ItemAttachmentRead,
    status_code=status.HTTP_201_CREATED,
)
def convert_item_photos_to_pdf_attachment(
    item_id: int,
    data: ItemImagesToPdfRequest,
    db: Session = Depends(get_db),
) -> ItemAttachmentRead:
    att = inventory_service.create_pdf_attachment_from_photos(db=db, item_id=item_id, data=data)
    return ItemAttachmentRead.model_validate(att)


@router.delete("/{item_id}/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item_attachment(item_id: int, attachment_id: int, db: Session = Depends(get_db)) -> None:
    inventory_service.delete_attachment(db=db, item_id=item_id, attachment_id=attachment_id)
    return None


@router.post("/{item_id}/find-image", response_model=FindItemImageResponse)
async def find_and_import_item_image(item_id: int, db: Session = Depends(get_db)) -> FindItemImageResponse:
    return await import_image_for_item(db.get_bind(), item_id)


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(item_id: int, db: Session = Depends(get_db)) -> None:
    inventory_service.delete(db, item_id)
    return None
