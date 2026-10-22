"""Compose the agent API route groups under their shared prefix."""

from fastapi import APIRouter

from app.api.agent_providers import router as provider_router
from app.api.agent_runs import router as run_router

router = APIRouter()
router.include_router(run_router)
router.include_router(provider_router)
