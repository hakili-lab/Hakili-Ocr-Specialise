"""
main.py
Point d'entrée de l'application FastAPI.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.routers import corrections, export, transcription
from app.services.job_store import purge_loop
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Démarre `job_store.purge_loop` en tâche de fond pour toute la durée de vie
    du process — la purge des jobs PDF expirés (voir job_store.py) ne serait
    sinon déclenchée qu'à la création d'un nouveau job, ce qui laisserait un
    job expiré en mémoire indéfiniment pendant une période sans trafic.
    Annulée proprement à l'arrêt de l'app.
    """
    settings = get_settings()
    task = asyncio.create_task(purge_loop(settings.JOB_PURGE_INTERVAL_SECONDS))
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="OCR Math API",
    description=(
        "API de transcription OCR de copies de mathématiques manuscrites en LaTeX, "
        "propulsée par l'API Claude d'Anthropic."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    # Pas de `allow_credentials=True` : l'auth est par header (X-API-Key), jamais
    # par cookie — le frontend fait des `fetch` sans `credentials: 'include'`
    # (voir hakili-ocr/src/services/apiClient.ts).
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(transcription.router)
app.include_router(corrections.router)
app.include_router(export.router)


@app.get("/", tags=["health"])
async def root() -> dict:
    """Endpoint racine, utile pour vérifier que le serveur tourne."""
    return {"status": "ok", "service": "ocr-math-api"}


@app.get("/health", tags=["health"])
async def health_check() -> dict:
    """Endpoint de health check."""
    return {"status": "healthy"}
