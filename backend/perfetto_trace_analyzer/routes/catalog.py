"""Catalog routes for frontend configuration."""

from fastapi import APIRouter

from ..services.catalog_service import CatalogService

router = APIRouter(prefix="/api", tags=["catalog"])
catalog_service = CatalogService()


@router.get("/analyzers")
async def list_analyzers():
    return {"analyzers": catalog_service.list_analyzers()}
