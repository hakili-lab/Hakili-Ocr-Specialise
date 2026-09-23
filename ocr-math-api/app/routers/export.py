"""
export.py
Endpoint POST /export/pdf : reçoit le HTML déjà rendu (React + KaTeX) de la
transcription depuis le frontend et retourne un vrai PDF vectoriel (texte
sélectionnable, pas une image rasterisée) généré par WeasyPrint — cf.
app/services/pdf_export_service.py pour le détail de l'approche.
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.models.schemas import ErrorResponse, ExportPdfRequest
from app.security import verify_api_key
from app.services.pdf_export_service import render_export_pdf

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/export", tags=["export"], dependencies=[Depends(verify_api_key)])


@router.post(
    "/pdf",
    responses={
        400: {"model": ErrorResponse, "description": "HTML manquant ou invalide"},
        500: {"model": ErrorResponse, "description": "Erreur de génération du PDF"},
    },
)
async def export_pdf(body: ExportPdfRequest) -> Response:
    try:
        # WeasyPrint (layout + rasterisation) est bloquant/CPU-bound : hors du
        # thread principal pour ne pas geler la boucle asyncio pendant la
        # génération, même pattern que correction_store dans corrections.py.
        pdf_bytes = await asyncio.to_thread(render_export_pdf, body.html)
    except Exception as exc:
        logger.exception("Échec de la génération du PDF d'export")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de la génération du PDF.",
        ) from exc

    return Response(content=pdf_bytes, media_type="application/pdf")
