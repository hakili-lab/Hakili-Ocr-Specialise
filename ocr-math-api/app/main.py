"""
main.py
Point d'entrée de l'application FastAPI.
"""

import logging

from fastapi import FastAPI

from app.config import get_settings
from app.routers import corrections, export, transcription
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

app = FastAPI(
    title="OCR Math API",
    description=(
        "API de transcription OCR de copies de mathématiques manuscrites en LaTeX, "
        "propulsée par l'API Claude d'Anthropic."
    ),
    version="0.1.0",
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    # Pas de `allow_credentials=True` : l'auth est par header (X-API-Key), jamais
    # par cookie — le frontend fait des `fetch` sans `credentials: 'include'`
    # (voir hakili-ocr/src/services/apiClient.ts). Revue sécurité 2026-08-28 (#7).
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
