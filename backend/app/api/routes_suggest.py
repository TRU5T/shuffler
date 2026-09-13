"""Balance suggestions: clean folder moves that even out free space."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..backends.base import StorageError
from ..models import SuggestionSet
from ..service import service

router = APIRouter()


class HideRequest(BaseModel):
    relpath: str


def _require_index():
    if service.index is None:
        raise HTTPException(status_code=409, detail="no active scan; run a scan first")
    return service.index


@router.get("/suggest", response_model=SuggestionSet)
def get_suggestions(
    exclude: list[str] = Query(default=[]),
    limit: int = Query(default=8, ge=1, le=16),
) -> SuggestionSet:
    _require_index()
    try:
        return service.suggestions(exclude=[p.strip("/") for p in exclude if p], limit=limit)
    except StorageError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/suggest/hide", response_model=SuggestionSet)
def hide_folder(request: HideRequest) -> SuggestionSet:
    _require_index()
    service.hide_suggestion(request.relpath)
    return service.suggestions()
