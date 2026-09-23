"""
schemas.py
Modèles Pydantic pour la validation stricte des données entrantes et sortantes.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

JobStatus = Literal["processing", "done", "error"]
"""État d'un job de transcription PDF — partagé entre `job_store.PDFJob` et `PDFJobStatusResponse`."""


class BoundingBox(BaseModel):
    """Coordonnées normalisées (0 à 1) d'un bloc dans l'image."""

    x_min: float = Field(..., ge=0, le=1)
    y_min: float = Field(..., ge=0, le=1)
    x_max: float = Field(..., ge=0, le=1)
    y_max: float = Field(..., ge=0, le=1)

    @field_validator("x_min", "y_min", "x_max", "y_max", mode="before")
    @classmethod
    def clamp_to_unit_range(cls, v: float) -> float:
        try:
            v = float(v)
        except (TypeError, ValueError):
            return v
        return max(0.0, min(1.0, v))

    @field_validator("x_max")
    @classmethod
    def x_max_must_exceed_x_min(cls, v: float, info) -> float:
        x_min = info.data.get("x_min")
        if x_min is not None and v < x_min:
            raise ValueError("x_max doit être supérieur ou égal à x_min")
        return v

    @field_validator("y_max")
    @classmethod
    def y_max_must_exceed_y_min(cls, v: float, info) -> float:
        y_min = info.data.get("y_min")
        if y_min is not None and v < y_min:
            raise ValueError("y_max doit être supérieur ou égal à y_min")
        return v


class Block(BaseModel):
    """Un bloc de contenu mathématique identifié dans la copie."""

    id: int
    label: str
    markdown: str
    bbox: BoundingBox
    confidence: int = Field(..., ge=0, le=100)


class OCRResult(BaseModel):
    """Résultat OCR d'une seule page."""

    blocks: list[Block]
    final_warning: Optional[str] = None


class PageResult(BaseModel):
    """Résultat OCR d'une page + image encodée."""

    page_number: int
    image_b64: str
    media_type: str
    width: int
    height: int
    ocr: OCRResult


class FailedPage(BaseModel):
    """Une page définitivement en échec (tentatives épuisées, troncature, rasterisation, ou job arrêté)."""

    page_number: int
    reason: str


class PDFTranscriptionResult(BaseModel):
    """Résultat complet d'un PDF multi-pages."""

    pages: list[PageResult]
    final_warning: Optional[str] = None
    failed_pages: list[FailedPage] = Field(default_factory=list)


class TranscriptionResponse(BaseModel):
    """Enveloppe de réponse de l'endpoint /transcribe (image unique)."""

    success: bool = True
    result: OCRResult


class ErrorResponse(BaseModel):
    """Format standard des erreurs renvoyées par l'API."""

    success: bool = False
    error: str
    detail: Optional[str] = None


class PDFJobStartResponse(BaseModel):
    """Réponse à la création d'un job de transcription PDF (traitement en arrière-plan)."""

    job_id: str
    pages_total: int


class PDFJobStatusResponse(BaseModel):
    """
    État d'avancement d'un job de transcription PDF, interrogé par polling.

    `result` peut être non-null AVANT que `status` passe à `"done"` : dès qu'au moins
    une page a été transcrite, l'endpoint expose un résultat partiel (les pages prêtes
    jusqu'ici, triées par `page_number`) pour permettre au frontend d'afficher/vérifier
    les premières pages pendant que les suivantes continuent d'être traitées en
    arrière-plan. `status` reste la seule source de vérité pour savoir si le document
    entier est terminé — ne pas déduire "terminé" de la simple présence de `result`.

    `fatal_error` peut lui aussi être non-null AVANT `"done"`/`"error"` : dès qu'une
    page rencontre une erreur indépendante du contenu (clé API invalide, crédit
    épuisé...), le job cesse de lancer de nouvelles pages mais laisse terminer celles
    déjà en vol — `status` reste `"processing"` jusqu'à leur fin, alors que
    `fatal_error` est déjà exploitable pour prévenir l'utilisateur sans attendre.

    `cancel_reason` suit la même logique que `fatal_error` mais pour un arrêt demandé
    (bouton Annuler, ou fermeture de l'onglet détectée côté frontend) plutôt que subi.
    """

    job_id: str
    status: JobStatus
    pages_done: int
    pages_total: int
    result: Optional[PDFTranscriptionResult] = None
    error: Optional[str] = None
    fatal_error: Optional[str] = None
    cancel_reason: Optional[str] = None


class PDFChunkedStartRequest(BaseModel):
    """
    Corps de POST /transcribe/pdf/start-chunked : ouvre un job PDF alimenté par plusieurs
    morceaux envoyés successivement, plutôt que par un seul fichier complet (voir
    POST /transcribe/pdf/{job_id}/chunk). `pages_expected` est une estimation annoncée par le
    client (le nombre de pages est connu côté client via pdf-lib avant tout envoi) — non fiable
    en soi, elle est plafonnée à MAX_PDF_PAGES côté serveur avant de devenir le budget de pages
    cumulé du job (job_store.create_chunked_job).
    """

    pages_expected: int = Field(..., gt=0)


class PDFChunkAckResponse(BaseModel):
    """
    Réponse à chaque POST /transcribe/pdf/{job_id}/chunk — un simple accusé de réception (le
    morceau a été reçu et sa rasterisation programmée), pas le résultat de la transcription :
    celui-ci n'est disponible que via GET /transcribe/pdf/status/{job_id} une fois `status == "done"`.
    """

    job_id: str
    pages_received: int
    status: JobStatus


class CorrectionResponse(BaseModel):
    """Confirmation d'enregistrement d'une correction (image + avant/après + erreur)."""

    id: str


class ExportPdfRequest(BaseModel):
    """Corps de POST /export/pdf : le fragment HTML déjà rendu (React + KaTeX) côté frontend."""

    html: str = Field(..., min_length=1)