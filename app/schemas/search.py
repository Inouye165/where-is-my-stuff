from typing import List
from pydantic import BaseModel, Field
from app.schemas.item import ItemRead


class SearchResult(BaseModel):
    query: str
    total: int
    items: List[ItemRead] = Field(default_factory=list)


class PhotoSearchResult(SearchResult):
    has_vision_api: bool = False

