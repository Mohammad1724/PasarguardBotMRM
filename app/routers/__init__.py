"""
Router initialization and FastAPI app setup.
"""

from fastapi import FastAPI

from app.version import VERSIONS

from .admin_app import router as admin_app_router
from .customer_app import router as customer_app_router
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


api_app.include_router(admin_app_router)
api_app.include_router(customer_app_router)


@api_app.middleware("http")
async def admin_security_headers(request, call_next):
    response = await call_next(request)
    if request.url.path == "/admin" or request.url.path.startswith("/admin/"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' https://telegram.org; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'self' https://web.telegram.org https://*.telegram.org"
        )
    return response
