"""
corrections.py
Endpoint POST /corrections : reçoit l'image croppée d'un bloc corrigé par
l'utilisateur (multipart) + les champs texte associés, et les délègue à
correction_store pour persistance (SQLite + fichier image sur disque).
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, status

from app.config import get_settings
from app.models.schemas import CorrectionResponse, ErrorResponse
from app.security import verify_api_key
from app.services.correction_store import save_correction
from app.utils.errors import log_unexpected
from app.utils.image_utils import validate_content_type, read_upload_with_limit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/corrections", tags=["corrections"], dependencies=[Depends(verify_api_key)])


@router.post(
    "",
    response_model=CorrectionResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Fichier ou champs invalides"},
        500: {"model": ErrorResponse, "description": "Erreur serveur"},
    },
)
async def create_correction(
    image: UploadFile,
    block_label: str = Form(...),
    confidence: int = Form(...),
    original_markdown: str = Form(...),
    corrected_markdown: str = Form(...),
    error_description: str = Form(...),
) -> CorrectionResponse:
    settings = get_settings()

    if not error_description.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La description de l'erreur est obligatoire.",
        )

    if image.content_type is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Impossible de déterminer le type du fichier envoyé.",
        )

    try:
        validate_content_type(image.content_type)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        image_bytes = await read_upload_with_limit(image, settings.MAX_IMAGE_SIZE_MB, label="Image")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        correction_id = await asyncio.to_thread(
            save_correction,
            image_bytes,
            block_label,
            confidence,
            original_markdown,
            corrected_markdown,
            error_description,
        )
    except Exception as exc:
        detail = log_unexpected(
            logger, "Échec de l'enregistrement de la correction", "Erreur interne du serveur."
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail
        ) from exc

    return CorrectionResponse(id=correction_id)
