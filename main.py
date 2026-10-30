import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from config import API_TITLE, API_VERSION, API_DESCRIPTION
from engine.model_loader import get_model_container
from engine.policy_engine import get_policy_engine
from engine.telemetry import get_telemetry_monitor
from api.product_routes import router
from engine.prototype_store import initialize_store
from config import BASE_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("payshield.main")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Application Startup: Preload LightGBM models & initialize policy engine
    logger.info("Initializing PayShield Backend Services...")
    initialize_store()
    model_container = get_model_container()
    loaded = model_container.load_models()
    if loaded:
        logger.info(f"Model Engine ready with {len(model_container.models)} trained fold models.")
    else:
        logger.warning("Models could not be preloaded on startup. Will attempt lazy-load on first inference.")

    policy_engine = get_policy_engine()
    logger.info(
        f"Dynamic Policy Engine active. Budget: {policy_engine.friction_budget:.2%}, "
        f"T_stepup: {policy_engine.stepup_threshold:.6f}, T_block: {policy_engine.block_threshold:.4f}"
    )

    yield

    # Application Shutdown
    logger.info("Shutting down PayShield Backend.")

app = FastAPI(
    title=API_TITLE,
    version=API_VERSION,
    description=API_DESCRIPTION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Allow the local frontend dev server. The built-in prototype UI is same-origin.
frontend_origins = [
    origin.strip() for origin in os.getenv(
        "PAYSHIELD_FRONTEND_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",") if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API routes
app.include_router(router, prefix="/api/v1", tags=["PayShield Core"])

@app.get("/app", include_in_schema=False)
async def prototype_app():
    return FileResponse(BASE_DIR / "product.html")


@app.get("/app.css", include_in_schema=False)
async def product_styles():
    return FileResponse(BASE_DIR / "app.css", media_type="text/css")


@app.get("/app.js", include_in_schema=False)
async def product_script():
    return FileResponse(BASE_DIR / "app.js", media_type="application/javascript")


@app.get("/", summary="Root Overview", tags=["System"])
async def root():
    policy = get_policy_engine()
    model_container = get_model_container()
    return {
        "service": "PayShield Payment Website Risk Assessment API",
        "version": API_VERSION,
        "status": "operational",
        "models_ready": model_container.is_ready,
        "models_count": len(model_container.models),
        "policy": {
            "friction_budget": policy.friction_budget,
            "stepup_threshold": policy.stepup_threshold,
            "block_threshold": policy.block_threshold,
            "fallback_active": policy.fallback_active,
        },
        "endpoints": {
            "interactive_docs": "/docs",
            "application": "/app",
            "redoc": "/redoc",
            "health": "/api/v1/health",
            "assess_url": "POST /api/v1/assessments/url",
            "transaction_integration": "POST /api/v1/integrations/transactions/score",
            "subscription": "POST /api/v1/billing/checkout-session",
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

