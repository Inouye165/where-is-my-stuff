from typing import List, Optional
from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.schemas.audit import AuditVerifyRequest
from app.schemas.common import ItemSortOrder, ItemStatus
from app.schemas.item import (
    ItemAttachmentRead,
    ItemBulkCreate,
    ItemCreate,
    ItemDetailRead,
    ItemGroupIntoBoxRequest,
    ItemGroupIntoBoxResponse,
    ItemRead,
    ItemUpdate,
)
from app.core.exceptions import NotFoundError
from app.services.image_lookup_service import find_and_import_image
from app.services.inventory_service import inventory_service

router = APIRouter(prefix="/items", tags=["Items"])


@router.post("", response_model=ItemRead, status_code=status.HTTP_201_CREATED)
def create_item(data: ItemCreate, db: Session = Depends(get_db)) -> ItemRead:
    item = inventory_service.create(db, data)
    return inventory_service.to_read_schema(item, db=db)


@router.post("/bulk", response_model=List[ItemRead], status_code=status.HTTP_201_CREATED)
def create_items_bulk(data: ItemBulkCreate, db: Session = Depends(get_db)) -> List[ItemRead]:
    items = inventory_service.bulk_create(db, data)
    return inventory_service.serialize_many(db, items)


@router.post("/group-into-box", response_model=ItemGroupIntoBoxResponse, status_code=status.HTTP_201_CREATED)
def group_items_into_box(data: ItemGroupIntoBoxRequest, db: Session = Depends(get_db)) -> ItemGroupIntoBoxResponse:
    return inventory_service.group_items_into_box(db, data)


@router.get("", response_model=List[ItemRead])
def list_items(
    category: Optional[str] = Query(default=None, max_length=80, description="Filter by category"),
    location_id: Optional[int] = Query(default=None, ge=1, description="Filter by direct location ID"),
    item_status: Optional[ItemStatus] = Query(default=None, alias="status", description="Filter by status"),
    sort: ItemSortOrder = Query(default=ItemSortOrder.last_seen_desc, description="Whitelisted sort order"),
    limit: int = Query(default=100, ge=1, le=20000),
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


@router.delete("/{item_id}/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item_attachment(item_id: int, attachment_id: int, db: Session = Depends(get_db)) -> None:
    inventory_service.delete_attachment(db=db, item_id=item_id, attachment_id=attachment_id)
    return None


@router.post("/{item_id}/find-image")
async def find_and_import_item_image(item_id: int, db: Session = Depends(get_db)) -> dict:
    item = inventory_service.get_detail(db, item_id)
    name = item.name
    part_number = item.serial_or_part_number
    category = item.category

    saved_path = await find_and_import_image(
        name=name,
        part_number=part_number,
        category=category,
    )
    if not saved_path:
        raise NotFoundError(f"No photo found online for '{name}'.")

    updated = inventory_service.update(db, item_id, ItemUpdate(item_photo=saved_path))
    item_read = inventory_service.to_read_schema(updated, db=db)
    return {
        "status": "ok",
        "item_photo": saved_path,
        "item": item_read.model_dump(mode="json"),
    }


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(item_id: int, db: Session = Depends(get_db)) -> None:
    inventory_service.delete(db, item_id)
    return None
