from fastapi import APIRouter

from app.api.routes import (
    brd,
    items,
    kb,
    login,
    private,
    projects,
    requirements,
    runs,
    users,
    utils,
)
from app.core.config import settings

api_router = APIRouter()
api_router.include_router(login.router)
api_router.include_router(users.router)
api_router.include_router(utils.router)
api_router.include_router(items.router)
api_router.include_router(projects.router)
api_router.include_router(runs.router)
api_router.include_router(kb.router)
api_router.include_router(brd.router)
api_router.include_router(requirements.router)


if settings.FASTAPI_ENV == "development":
    api_router.include_router(private.router)
