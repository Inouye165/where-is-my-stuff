from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.schemas.search import PhotoSearchResult, SearchResult
from app.services.search_service import search_service

router = APIRouter(prefix="/search", tags=["Search"])


@router.get("", response_model=SearchResult)
def search_inventory(
    q: str = Query(default="", max_length=300, description="Keyword search across items, tags, categories, and locations"),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> SearchResult:
    items = search_service.search(db, query=q, limit=limit)
    return SearchResult(query=q, total=len(items), items=items)


@router.post("/photo", response_model=PhotoSearchResult)
async def search_by_photo(
    file: UploadFile = File(...),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> PhotoSearchResult:
    result = await search_service.search_by_photo(db, file=file, limit=limit)
    return PhotoSearchResult(**result)
