from fastapi import APIRouter

from app.api.routes import (
    brd,
    dashboard,
    exports,
    integrations,
    items,
    kb,
    login,
    mapping,
    private,
    projects,
    requirements,
    runs,
    tests,
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
api_router.include_router(dashboard.router)
api_router.include_router(integrations.router)
api_router.include_router(runs.router)
api_router.include_router(kb.router)
api_router.include_router(brd.router)
api_router.include_router(requirements.router)
api_router.include_router(mapping.router)
api_router.include_router(tests.router)
api_router.include_router(exports.router)


if settings.FASTAPI_ENV == "development":
    api_router.include_router(private.router)
