"""
Router initialization and FastAPI app setup.
"""

from fastapi import FastAPI

from app.version import VERSIONS

from .payments import payment_router
from .webhook import webhook_router

# Create FastAPI application
api_app = FastAPI(
    title="PasarguardBot API",
    version=VERSIONS.app,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

api_app.include_router(webhook_router, prefix="/api", tags=["Webhook"])

__all__ = ["api_app"]


api_app.include_router(payment_router, prefix="/api", tags=["Payments"])
