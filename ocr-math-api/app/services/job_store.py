"""
job_store.py
Suivi en mémoire des jobs de transcription PDF en arrière-plan, pour permettre au frontend
de suivre la progression (nombre de pages traitées) par polling au lieu d'attendre une seule
requête HTTP bloquante jusqu'à la fin.

Limite connue : store en mémoire du process — convient à un serveur mono-process (dev / usage
personnel). Pour un déploiement multi-workers, il faudrait un store partagé (Redis, etc.).
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from app.config import get_settings
from app.models.schemas import JobStatus, PageResult, PDFTranscriptionResult


@dataclass
class PDFJob:
    job_id: str
    pages_total: int
    pages_done: int = 0
    status: JobStatus = "processing"
    result: Optional[PDFTranscriptionResult] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)

    # --- Champs utilisés uniquement par un job "chunké" (upload par morceaux,
    # voir POST /transcribe/pdf/start-chunked + /pdf/{job_id}/chunk) : un job
    # créé par le flux classique (create_job) les laisse à leurs valeurs par
    # défaut et ne les touche jamais. ---

    # Annoncé par le client à la création, plafonné à MAX_PDF_PAGES. None
    # distingue un job "legacy" (fichier complet en un seul POST) d'un job
    # chunké — pas besoin d'un booléen séparé pour ça.
    pages_expected: Optional[int] = None
    # Pages réellement rasterisées jusqu'ici, tous morceaux confondus — calculé
    # côté serveur au fil de la réception, jamais fait confiance au client.
    pages_received: int = 0
    # Octets cumulés reçus, tous morceaux confondus (budget MAX_PDF_SIZE_MB
    # appliqué de façon cumulative plutôt qu'en un seul appel).
    bytes_received: int = 0
    # Nombre de groupes de tâches de traitement de morceau actuellement en vol
    # (un morceau peut encore être en cours d'OCR pendant que le suivant
    # arrive) — sert à détecter la fin du job sans `gather` monolithique.
    chunks_pending: int = 0
    # True une fois le morceau marqué is_last_chunk=True accepté : plus aucun
    # morceau ne sera reçu après, mais des tâches de traitement peuvent encore
    # être en vol (voir chunks_pending).
    upload_finalized: bool = False
    # Rafraîchi à chaque morceau reçu et à chaque tâche de traitement terminée
    # — sert à détecter un job chunké bloqué (JOB_STALL_TIMEOUT_SECONDS).
    updated_at: float = field(default_factory=time.time)
    # Résultats par page, clé = page_number (1-based) — même type que celui
    # utilisé en local par _run_pdf_job pour un job "legacy" (results: dict[int,
    # PageResult] = {}), mais promu en champ de job ici car un job chunké
    # l'alimente au fil de plusieurs vagues de tâches successives (une par
    # morceau reçu), pas d'un seul appel de fonction.
    results: dict[int, PageResult] = field(default_factory=dict)
    warnings: list[tuple[int, str]] = field(default_factory=list)
    errors: list[tuple[int, str]] = field(default_factory=list)
    # Empêche deux morceaux d'être lus/comptés en même temps pour un même job
    # (cf. Danger 3 du plan : requêtes concurrentes/désordonnées) — tenu
    # uniquement pendant la portion lecture + comptage bon marché des pages du
    # handler (voir count_pdf_pages), pas pendant la rasterisation ni le
    # traitement OCR qui suivent, déportés en tâche de fond. Sûr à construire ici via
    # `field(default_factory=asyncio.Lock)` : contrairement au sémaphore
    # global paresseux de claude_service.py (qui doit se lier après qu'une
    # boucle d'événements existe, potentiellement avant qu'aucune requête
    # n'arrive), un PDFJob n'est jamais instancié ailleurs que dans un handler
    # de requête FastAPI — donc toujours déjà à l'intérieur d'une boucle
    # d'événements en cours. Ne pas "corriger" ça en le rendant paresseux.
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)


_jobs: dict[str, PDFJob] = {}


def _purge_expired_jobs() -> None:
    """
    Supprime deux catégories de jobs devenus inutiles :
    - jobs terminés (done/error) plus vieux que JOB_TTL_SECONDS ;
    - jobs "chunkés" bloqués : upload jamais finalisé (upload_finalized=False)
      et sans nouveau morceau reçu depuis plus de JOB_STALL_TIMEOUT_SECONDS —
      un client qui abandonne un upload par morceaux en cours de route
      laisserait sinon un job "processing" indéfiniment impurgeable, puisque
      rien ne le fait jamais passer à done/error.
    Appelé à chaque création de job plutôt que via une tâche planifiée à part :
    le store est en mémoire de process, donc un balayage opportuniste au fil
    des créations suffit à empêcher sa croissance indéfinie.
    """
    settings = get_settings()
    now = time.time()
    expired = [
        job_id
        for job_id, job in _jobs.items()
        if (job.status in ("done", "error") and now - job.created_at > settings.JOB_TTL_SECONDS)
        or (
            job.status == "processing"
            and not job.upload_finalized
            and now - job.updated_at > settings.JOB_STALL_TIMEOUT_SECONDS
        )
    ]
    for job_id in expired:
        del _jobs[job_id]


def create_job(pages_total: int) -> PDFJob:
    """Crée un job "legacy" : le fichier PDF complet est déjà connu en un seul POST (/pdf/start)."""
    _purge_expired_jobs()
    job = PDFJob(job_id=uuid.uuid4().hex, pages_total=pages_total)
    _jobs[job.job_id] = job
    return job


def create_chunked_job(pages_expected: int) -> PDFJob:
    """
    Crée un job PDF alimenté par plusieurs requêtes (upload par morceaux,
    POST /transcribe/pdf/start-chunked puis /pdf/{job_id}/chunk) plutôt que
    par un fichier déjà complet. `pages_expected` vient du client et N'EST PAS
    fiable (un client pourrait annoncer une valeur trop basse pour tenter de
    dépasser MAX_PDF_PAGES en étalant les pages sur plusieurs petits
    morceaux) : on le plafonne immédiatement à MAX_PDF_PAGES, et cette valeur
    plafonnée devient le budget de pages cumulé du job pour tous les morceaux
    à venir (voir le calcul de remaining_pages dans le routeur), pas
    seulement une estimation d'affichage.
    """
    _purge_expired_jobs()
    settings = get_settings()
    capped = min(pages_expected, settings.MAX_PDF_PAGES)
    job = PDFJob(job_id=uuid.uuid4().hex, pages_total=capped, pages_expected=capped)
    _jobs[job.job_id] = job
    return job


def get_job(job_id: str) -> Optional[PDFJob]:
    return _jobs.get(job_id)
