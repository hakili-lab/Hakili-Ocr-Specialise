"""
transcription.py
Endpoints POST /transcribe (image) et POST /transcribe/pdf/start + GET /transcribe/pdf/status/{job_id}
(PDF, traité en arrière-plan avec suivi de progression par polling).
"""

import asyncio
import logging
import time

import anthropic
from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, status

from app.config import get_settings
from app.models.schemas import (
    ErrorResponse,
    TranscriptionResponse,
    PDFJobStartResponse,
    PDFJobStatusResponse,
    PDFTranscriptionResult,
    PageResult,
    PDFChunkedStartRequest,
    PDFChunkAckResponse,
)
from app.security import verify_api_key
from app.services.claude_service import call_anthropic_ocr, describe_anthropic_error, get_anthropic_semaphore
from app.services.job_store import create_job, create_chunked_job, get_job, PDFJob
from app.utils.errors import log_unexpected
from app.utils.image_utils import (
    encode_bytes_to_base64,
    validate_content_type,
    resize_for_vision,
    normalize_orientation,
    read_upload_with_limit,
    read_upload_with_byte_limit,
    convert_pdf_to_images,
    count_pdf_pages,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/transcribe", tags=["transcription"], dependencies=[Depends(verify_api_key)])

# asyncio ne garde qu'une référence faible aux tasks créées par create_task : sans
# rien d'autre qui la référence, une task "fire-and-forget" peut être supprimée par
# le garbage collector en plein milieu de son exécution. On garde donc une référence
# forte le temps du job, retirée automatiquement à la fin via add_done_callback.
_background_tasks: set[asyncio.Task] = set()


async def _process_single_image(raw_bytes: bytes, media_type: str):
    """
    Pipeline commun : orientation → resize → base64 → Claude.
    `normalize_orientation`/`resize_for_vision` (PIL) et `encode_bytes_to_base64`
    sont des appels CPU synchrones ; les passer par `asyncio.to_thread` évite
    qu'ils ne bloquent la boucle d'événements pendant leur exécution (impact
    direct sur les autres requêtes concurrentes, ex. le polling de statut d'un
    autre job PDF).

    Le prétraitement est fait sous le même sémaphore que l'appel Claude
    (`get_anthropic_semaphore`), mais dans une section à part, relâchée avant
    l'appel réseau — jamais imbriquée avec l'acquisition interne de
    `call_anthropic_ocr` (un `asyncio.Semaphore` n'est pas réentrant). Sans
    cette borne, un lot entier de pages (`asyncio.gather`) prétraiterait
    d'un coup, alors que seules `ANTHROPIC_CONCURRENCY` d'entre elles peuvent
    de toute façon être envoyées à Claude en même temps : les images déjà
    redimensionnées/encodées des pages en attente s'accumuleraient en
    mémoire pour rien.
    """
    semaphore = get_anthropic_semaphore()
    async with semaphore:
        raw_bytes = await asyncio.to_thread(normalize_orientation, raw_bytes, media_type)
        raw_bytes, image_width, image_height = await asyncio.to_thread(
            resize_for_vision, raw_bytes, media_type
        )
        image_b64 = await asyncio.to_thread(encode_bytes_to_base64, raw_bytes)
    ocr_result = await call_anthropic_ocr(image_b64, media_type, image_width, image_height)
    return ocr_result, image_b64, image_width, image_height


@router.post(
    "",
    response_model=TranscriptionResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Fichier invalide"},
        422: {"model": ErrorResponse, "description": "Réponse Claude invalide"},
        500: {"model": ErrorResponse, "description": "Erreur serveur ou API Anthropic"},
    },
)
async def transcribe_image(file: UploadFile) -> TranscriptionResponse:
    settings = get_settings()

    if file.content_type is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Impossible de déterminer le type du fichier envoyé.",
        )

    try:
        media_type = validate_content_type(file.content_type)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        raw_bytes = await read_upload_with_limit(file, settings.MAX_IMAGE_SIZE_MB, label="Image")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        ocr_result, _, _, _ = await _process_single_image(raw_bytes, media_type)
    except ValueError as exc:
        logger.error("Réponse Claude invalide : %s", exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except RuntimeError as exc:
        logger.error("Erreur de configuration : %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc
    except anthropic.APIError as exc:
        logger.exception("Erreur API Anthropic")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=describe_anthropic_error(exc),
        ) from exc
    except Exception as exc:
        detail = log_unexpected(
            logger, "Erreur inattendue lors de la transcription", "Erreur interne du serveur."
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail
        ) from exc

    return TranscriptionResponse(success=True, result=ocr_result)


async def _process_page_and_track(
    page_number: int,
    img_bytes: bytes,
    job: PDFJob,
    results: dict[int, PageResult],
    warnings: list[tuple[int, str]],
    errors: list[tuple[int, str]],
) -> None:
    """
    Traite une page et écrit son résultat dans `results[page_number]` — un
    dict indexé par numéro de page (pas par ordre d'arrivée), car les tâches,
    lancées en parallèle par `asyncio.gather`, terminent dans un ordre
    arbitraire : le résultat final doit être reconstruit par numéro de page,
    jamais par ordre de complétion. Réutilisé tel quel par les deux flux PDF :
    `_run_pdf_job` (flux "legacy", un seul appel avec toutes les pages
    connues d'avance) et `_process_chunk_pages` (flux "chunké", un appel par
    morceau reçu — `results`/`warnings`/`errors` sont alors les champs
    persistants de `job`, accumulés au fil de plusieurs vagues successives,
    pas des listes locales à un seul appel).

    Les pages obtiennent une place du sémaphore Anthropic dans l'ordre
    d'arrivée à la file (FIFO, `claude_service._get_semaphore`), sans notion
    de priorité par numéro de page — une page peut donc finir avant une autre
    de numéro inférieur ; c'est volontaire, voir `useTranscribe.ts` côté
    frontend, qui affiche chaque page dès qu'elle est prête en indiquant son
    propre numéro, sans exiger l'ordre.

    `job.pages_done` est incrémenté ici, en effet de bord, dès la fin de CETTE
    page — pas après qu'un `gather` englobant ait fini d'attendre toutes les
    pages d'un coup. Comme `GET /pdf/status/{job_id}` lit `job.pages_done`
    directement sur l'objet partagé, la progression avance en temps réel
    pendant que d'autres pages sont encore en cours, sans qu'aucun verrou ne
    soit nécessaire (une seule boucle d'événements : rien d'autre ne peut
    s'exécuter entre la lecture et l'écriture de `job.pages_done`, ni entre
    lecture et écriture d'une clé de `results`).
    """
    media_type = "image/png"  # convert_pdf_to_images produit du PNG
    try:
        ocr_result, image_b64, w, h = await _process_single_image(img_bytes, media_type)
    except anthropic.APIError as exc:
        detail = describe_anthropic_error(exc)
        logger.error("Échec transcription page %d : %s", page_number, detail)
        warnings.append((page_number, f"Page {page_number} : {detail}"))
        errors.append((page_number, detail))
    except Exception as exc:
        # Erreur inattendue (pas une anthropic.APIError — ex. bug de parsing) : trace
        # complète côté serveur, mais on ne remonte au frontend (via warnings/final_warning
        # et via errors → job.error si TOUTES les pages échouent) que le *type* d'erreur,
        # jamais son message — celui-ci peut contenir chemins, schéma SQLite ou bouts de
        # l'entrée. L'échec reste visible et typé, pas muet, sans divulguer d'interne.
        logger.exception("Échec transcription page %d", page_number)
        label = f"échec de la transcription ({type(exc).__name__})"
        warnings.append((page_number, f"Page {page_number} : {label}"))
        errors.append((page_number, label))
    else:
        if ocr_result.final_warning:
            warnings.append((page_number, f"Page {page_number} : {ocr_result.final_warning}"))
        results[page_number] = PageResult(
            page_number=page_number,
            image_b64=image_b64,
            media_type=media_type,
            width=w,
            height=h,
            ocr=ocr_result,
        )
    finally:
        job.pages_done += 1


def _build_pdf_result(
    results: dict[int, PageResult], warnings: list[tuple[int, str]]
) -> PDFTranscriptionResult:
    """
    Construit un `PDFTranscriptionResult` trié par numéro de page à partir des résultats
    accumulés jusqu'ici. Utilisé aussi bien pour le résultat final (`_finalize_pdf_job`,
    une fois toutes les pages traitées) que pour un résultat PARTIEL exposé par
    `get_pdf_transcription_status` pendant que le job est encore `"processing"` — ce
    qui permet au frontend d'afficher/vérifier les premières pages dès qu'elles sont
    prêtes, sans attendre la fin du document entier.
    """
    pages = sorted(results.values(), key=lambda p: p.page_number)
    ordered_warnings = [msg for _pn, msg in sorted(warnings, key=lambda item: item[0])]
    return PDFTranscriptionResult(
        pages=pages,
        final_warning="\n".join(ordered_warnings) if ordered_warnings else None,
    )


def _finalize_pdf_job(job: PDFJob, results: dict[int, PageResult], warnings: list[tuple[int, str]],
                       errors: list[tuple[int, str]]) -> None:
    """
    Fabrique `job.result`/`job.status` à partir des résultats/avertissements/erreurs
    accumulés. Partagé par `_run_pdf_job` (fin du seul `gather`) et
    `_maybe_finalize_job` (fin du dernier morceau d'un job chunké) pour que les deux
    flux produisent un `PDFTranscriptionResult` strictement identique en forme.

    Rafraîchit `job.updated_at` : c'est ce timestamp (pas `job.created_at`, qui ne
    bouge plus une fois le job créé) que `_purge_expired_jobs` (`job_store.py`) compare
    à `JOB_TTL_SECONDS` pour décider quand purger un job terminé. Ancrer le TTL sur la
    création plutôt que sur la fin du traitement ferait purger un job dont le
    traitement a duré plus longtemps que JOB_TTL_SECONDS dès l'instant où il se
    termine — potentiellement avant même que le frontend n'ait fait son dernier poll.
    """
    job.updated_at = time.time()
    if not results:
        job.status = "error"
        # Dernier message par ordre de page (pas de complétion) réutilisé
        # comme job.error si AUCUNE page n'a réussi — sinon un job "done"
        # avec pages=[] laisse le frontend sur un écran vide sans message
        # ni moyen de revenir en arrière (AppContext.tsx :
        # transcriptionResult devient null, ResultScreen ne s'affiche jamais).
        job.error = (
            sorted(errors, key=lambda item: item[0])[-1][1]
            if errors
            else "La transcription a échoué pour toutes les pages du document."
        )
        return

    job.result = _build_pdf_result(results, warnings)
    job.status = "done"


async def _run_pdf_job(job_id: str, page_images: list[tuple[bytes, int, int]]) -> None:
    """
    Traite les pages d'un PDF en parallèle via `asyncio.gather` — le nombre
    d'appels Anthropic réellement en vol reste borné par le sémaphore global
    de `claude_service.py` (`ANTHROPIC_CONCURRENCY`), pas par cette fonction :
    `gather` démarre toutes les tâches immédiatement, mais chacune n'entre
    dans son appel Claude qu'une fois une place de sémaphore obtenue.
    """
    job = get_job(job_id)
    if job is None:
        return

    # Écrit directement dans les champs `job.results`/`job.warnings`/`job.errors`
    # (plutôt que des dicts/listes locaux à cet appel) pour que
    # `get_pdf_transcription_status` puisse exposer un résultat partiel — les pages
    # déjà transcrites — pendant que `gather` attend encore les pages restantes.
    try:
        await asyncio.gather(*(
            _process_page_and_track(idx + 1, img_bytes, job, job.results, job.warnings, job.errors)
            for idx, (img_bytes, _orig_w, _orig_h) in enumerate(page_images)
        ))
        _finalize_pdf_job(job, job.results, job.warnings, job.errors)
    except Exception:
        # `job.error` est renvoyé au frontend (PDFJobStatusResponse) : trace complète
        # côté serveur, message générique au client — cf. app/utils/errors.py.
        logger.exception("Échec inattendu du job PDF %s", job_id)
        job.status = "error"
        job.error = "Erreur interne du serveur pendant le traitement du document."
        # Ce chemin ne passe pas par `_finalize_pdf_job` (qui rafraîchit déjà
        # `updated_at`) — sans ça, `_purge_expired_jobs` (job_store.py)
        # mesurerait le TTL depuis `created_at` pour ce job, pas depuis sa
        # fin réelle.
        job.updated_at = time.time()


async def _process_chunk_pages(
    job_id: str, chunk_bytes: bytes, start_page_number: int, page_count: int
) -> None:
    """
    Traite les pages d'UN morceau reçu par POST /pdf/{job_id}/chunk. Rasterise
    d'abord ce morceau elle-même (PyMuPDF, `asyncio.to_thread`) puis lance l'OCR
    de ses pages — même motif que `_run_pdf_job` pour l'OCR (un `asyncio.gather`
    sur `_process_page_and_track` par page), mais écrit dans
    `job.results`/`job.warnings`/`job.errors` (partagés entre TOUS les morceaux
    du job) plutôt que dans des listes locales à cet appel : un job chunké
    accumule ses résultats au fil de plusieurs vagues de tâches successives,
    une par morceau reçu, pas d'un seul `gather` englobant tout le document.

    La rasterisation se fait ici, en tâche de fond, plutôt que dans
    `upload_pdf_chunk` avant de répondre au client : `upload_pdf_chunk` ne fait
    qu'une validation bon marché du nombre de pages (`count_pdf_pages`, qui
    n'ouvre que la structure du PDF, sans rendu de pixel) avant de répondre,
    pour que la lecture du morceau reste rapide indépendamment du volume de
    travail que cette tâche de fond doit ensuite abattre.

    `job.processing_semaphore` (acquis par `upload_pdf_chunk` avant même de
    créer cette tâche) borne le nombre de morceaux traités en parallèle pour ce
    job — relâché ici, dans le `finally`, une fois ce morceau terminé. Comme le
    client envoie ses morceaux un par un en attendant la réponse de chacun,
    relâcher cette place est ce qui débloque l'acceptation du morceau suivant.

    Décrémente aussi `job.chunks_pending` et tente la finalisation du job à sa
    propre fin — voir `_maybe_finalize_job` — que la rasterisation ait réussi
    ou non, pour qu'un morceau dont le rendu échoue ne bloque jamais
    indéfiniment la finalisation du job.
    """
    job = get_job(job_id)
    if job is None:
        return
    try:
        try:
            page_images = await asyncio.to_thread(convert_pdf_to_images, chunk_bytes, dpi=150)
        except Exception:
            # Le budget de pages a déjà été validé (count_pdf_pages, dans
            # upload_pdf_chunk) avant que cette tâche ne soit créée — un échec ici
            # est un problème de rendu (page corrompue, etc.), pas de dépassement
            # de budget. Comme un seul appel `convert_pdf_to_images` couvre tout
            # le morceau, on ne sait pas quelle(s) page(s) précise(s) ont fait
            # échouer le rendu : toutes les pages annoncées pour ce morceau sont
            # donc marquées en échec, avec le même traitement que
            # `_process_page_and_track` applique à l'échec d'une page individuelle
            # (warnings + errors + pages_done incrémenté), pour que la
            # comptabilité de progression et la finalisation du job restent
            # cohérentes.
            logger.exception("Échec de la rasterisation en tâche de fond d'un morceau (job %s)", job_id)
            message = "Échec de la conversion de ce morceau (fichier peut-être corrompu ou protégé)."
            for offset in range(page_count):
                page_number = start_page_number + offset
                job.warnings.append((page_number, f"Page {page_number} : {message}"))
                job.errors.append((page_number, message))
                job.pages_done += 1
            return

        await asyncio.gather(*(
            _process_page_and_track(start_page_number + i, img_bytes, job, job.results, job.warnings, job.errors)
            for i, (img_bytes, _w, _h) in enumerate(page_images)
        ))
    finally:
        job.chunks_pending -= 1
        job.updated_at = time.time()
        job.processing_semaphore.release()
        _maybe_finalize_job(job)


def _maybe_finalize_job(job: PDFJob) -> None:
    """
    Appelée à la fin du traitement de CHAQUE morceau (pas seulement le
    dernier envoyé) : le morceau dont le traitement se termine EN DERNIER
    dans le temps réel n'est pas forcément celui envoyé en dernier (un petit
    morceau envoyé tôt peut finir de traiter après un gros morceau envoyé
    plus tard). Le job passe à done/error dès que les deux conditions sont
    réunies : plus aucun morceau en vol (`chunks_pending == 0`) ET le client
    a signalé qu'il n'enverra plus de morceau (`upload_finalized`). Aucun
    `gather` global n'attend ça ici : chaque appelant (`_process_chunk_pages`)
    vérifie juste, à sa propre fin, s'IL est celui qui fait passer
    `chunks_pending` à 0 — sûr sans verrou car aucun `await` ne s'intercale
    entre la décrémentation et ce test (une seule boucle d'événements).

    Le `try/except` autour de `_finalize_pdf_job` est nécessaire : c'est
    appelé depuis le `finally` de `_process_chunk_pages`, une tâche de fond
    fire-and-forget — sans lui, une exception inattendue ici (ex. donnée
    corrompue dans `_build_pdf_result`) s'échapperait silencieusement (juste
    un "Task exception was never retrieved" d'asyncio, invisible pour
    `/pdf/status`) et laisserait `job.status` bloqué sur `"processing"` POUR
    TOUJOURS : ni le TTL (ne s'applique qu'à done/error) ni le
    stall-timeout (exige `upload_finalized=False`, déjà `True` ici) ne
    peuvent alors purger le job — fuite mémoire permanente doublée d'un
    polling frontend qui ne s'arrête jamais. Même pattern que le
    `try/except` de `_run_pdf_job` pour le flux legacy, qui est lui déjà
    protégé (son `try` englobe l'appel à `_finalize_pdf_job`).
    """
    if job.status != "processing" or not job.upload_finalized or job.chunks_pending > 0:
        return
    job.pages_total = job.pages_received  # corrige l'estimation pages_expected par le compte réel
    try:
        _finalize_pdf_job(job, job.results, job.warnings, job.errors)
    except Exception:
        logger.exception("Échec inattendu de la finalisation du job PDF chunké %s", job.job_id)
        job.status = "error"
        job.error = "Erreur interne du serveur pendant la finalisation du document."
        job.updated_at = time.time()


@router.post(
    "/pdf/start",
    response_model=PDFJobStartResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Fichier invalide"},
    },
)
async def start_pdf_transcription(file: UploadFile) -> PDFJobStartResponse:
    """
    Reçoit un PDF, le décompose en images, démarre le traitement en arrière-plan
    et retourne immédiatement un job_id à interroger via GET /pdf/status/{job_id}.
    """
    settings = get_settings()

    if file.content_type is None or "pdf" not in file.content_type.lower():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le fichier doit être un PDF.",
        )

    try:
        raw_bytes = await read_upload_with_limit(file, settings.MAX_PDF_SIZE_MB, label="PDF")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        # convert_pdf_to_images (PyMuPDF) est un appel CPU synchrone ; via
        # asyncio.to_thread pour ne pas geler la boucle d'événements le temps
        # de rasteriser potentiellement des centaines de pages.
        page_images = await asyncio.to_thread(
            convert_pdf_to_images, raw_bytes, dpi=150, max_pages=settings.MAX_PDF_PAGES
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Échec de la conversion PDF")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Impossible de convertir le PDF. Le fichier est peut-être corrompu ou protégé.",
        ) from exc

    if not page_images:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le PDF ne contient aucune page.",
        )

    job = create_job(pages_total=len(page_images))
    task = asyncio.create_task(_run_pdf_job(job.job_id, page_images))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return PDFJobStartResponse(job_id=job.job_id, pages_total=job.pages_total)


@router.get(
    "/pdf/status/{job_id}",
    response_model=PDFJobStatusResponse,
    responses={404: {"model": ErrorResponse, "description": "Job introuvable"}},
)
async def get_pdf_transcription_status(job_id: str) -> PDFJobStatusResponse:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job introuvable.")

    # `job.result` n'est posé qu'à la finalisation (status "done"/"error"). Tant que le
    # job est encore "processing", on construit un résultat PARTIEL à la volée à partir
    # des pages déjà accumulées dans `job.results` — permet au frontend d'afficher/
    # vérifier les premières pages sans attendre la fin du document entier. Coût
    # négligeable : un simple tri du dict déjà en mémoire, à chaque appel de polling.
    result = job.result
    if result is None and job.status == "processing" and job.results:
        result = _build_pdf_result(job.results, job.warnings)

    return PDFJobStatusResponse(
        job_id=job.job_id,
        status=job.status,
        pages_done=job.pages_done,
        pages_total=job.pages_total,
        result=result,
        error=job.error,
    )


# ─── Upload PDF par morceaux (chunked) ──────────────────────────────────
#
# Alternative à /pdf/start pour les gros documents : au lieu d'envoyer le
# fichier entier en un seul POST bloquant (upload puis traitement,
# strictement séquentiels), le client découpe le PDF en sous-PDF côté client
# (pdf-lib, aucun rendu de pixel) et les envoie un par un via /pdf/{job_id}/chunk.
# Le backend rasterise et lance le traitement Claude de chaque morceau dès
# réception, sans attendre les suivants — le traitement du morceau N se
# poursuit en tâche de fond pendant que le morceau N+1 est envoyé. Réutilise
# la même GET /pdf/status/{job_id} que le flux classique : aucun changement
# nécessaire côté polling.


@router.post(
    "/pdf/start-chunked",
    response_model=PDFJobStartResponse,
    responses={
        422: {"model": ErrorResponse, "description": "pages_expected invalide"},
    },
)
async def start_chunked_pdf_transcription(body: PDFChunkedStartRequest) -> PDFJobStartResponse:
    """
    Ouvre un job PDF alimenté par plusieurs morceaux envoyés successivement
    (voir POST /pdf/{job_id}/chunk) plutôt que par un fichier complet en un
    seul POST — pensé pour les gros documents, où attendre la fin de
    l'upload avant de commencer le traitement gaspille du temps (upload et
    traitement Claude peuvent alors se chevaucher au lieu de s'enchaîner).
    Ne reçoit aucun fichier : juste le nombre de pages annoncé par le client
    (connu côté client via pdf-lib avant tout envoi). `create_chunked_job`
    plafonne cette valeur à MAX_PDF_PAGES avant d'en faire le budget de
    pages cumulé du job pour tous les morceaux à venir.
    """
    job = create_chunked_job(pages_expected=body.pages_expected)
    return PDFJobStartResponse(job_id=job.job_id, pages_total=job.pages_total)


@router.post(
    "/pdf/{job_id}/chunk",
    response_model=PDFChunkAckResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Morceau invalide ou budget dépassé"},
        404: {"model": ErrorResponse, "description": "Job introuvable"},
        409: {"model": ErrorResponse, "description": "Job non receveur ou morceau concurrent"},
    },
)
async def upload_pdf_chunk(
    job_id: str,
    file: UploadFile,
    is_last_chunk: bool = Form(False),
) -> PDFChunkAckResponse:
    """
    Reçoit un morceau (sous-PDF, quelques pages) d'un job ouvert par
    /pdf/start-chunked. Les morceaux doivent être envoyés strictement l'un
    après l'autre (le client n'envoie le morceau N+1 qu'une fois la réponse
    du morceau N reçue) — c'est ce qui permet au traitement Claude du
    morceau N de continuer en tâche de fond pendant que N+1 est en cours
    d'envoi, sans jamais avoir besoin d'un numéro d'ordre annoncé par le
    client : `job.pages_received` est l'unique source de vérité sur la
    numérotation des pages, calculée côté serveur.

    `job.lock` rejette activement (409) tout morceau qui arriverait pendant
    que le précédent est encore en cours de lecture/comptage de pages pour ce
    même job, plutôt que de le mettre en file d'attente silencieusement —
    un tel chevauchement signale une violation du contrat d'envoi séquentiel
    côté client (bug, double-clic, requête retentée) qu'il vaut mieux
    remonter que masquer. Le lock ne couvre plus que la lecture des octets et
    le comptage bon marché des pages (`count_pdf_pages`) — la rasterisation
    et l'OCR, plus coûteux, se font hors lock dans la tâche de fond lancée
    juste après (`_process_chunk_pages`), pour ne pas retarder la réponse au
    client plus que nécessaire.
    """
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job introuvable.")

    if job.pages_expected is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ce job n'a pas été ouvert en mode upload par morceaux.",
        )
    if job.status != "processing" or job.upload_finalized:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ce job n'accepte plus de nouveaux morceaux.",
        )
    if job.lock.locked():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Un morceau est déjà en cours de réception pour ce job — envoyez les morceaux un par un.",
        )

    if file.content_type is None or "pdf" not in file.content_type.lower():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le morceau doit être un PDF.",
        )

    settings = get_settings()
    async with job.lock:
        remaining_bytes = max(0, int(settings.MAX_PDF_SIZE_MB * 1024 * 1024) - job.bytes_received)
        try:
            chunk_bytes = await read_upload_with_byte_limit(file, remaining_bytes, label="Morceau PDF")
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        remaining_pages = max(0, job.pages_expected - job.pages_received)
        try:
            # count_pdf_pages n'ouvre que la structure du PDF (table des pages), sans
            # rendre le moindre pixel — nettement moins coûteux que convert_pdf_to_images.
            # Le vrai rendu (coûteux) est différé dans _process_chunk_pages, en tâche de
            # fond, pour ne pas retarder la réponse HTTP : le client n'envoie le morceau
            # N+1 qu'après avoir reçu cette réponse (contrat d'envoi séquentiel), donc tout
            # ce qui reste dans ce bloc bloque directement l'envoi du morceau suivant.
            page_count = await asyncio.to_thread(
                count_pdf_pages, chunk_bytes, max_pages=remaining_pages
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Échec de la lecture d'un morceau PDF (job %s)", job_id)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Impossible de lire ce morceau. Le fichier est peut-être corrompu ou protégé.",
            ) from exc

        start_page_number = job.pages_received + 1
        job.pages_received += page_count
        job.bytes_received += len(chunk_bytes)
        if is_last_chunk:
            job.upload_finalized = True
        job.chunks_pending += 1
        job.updated_at = time.time()

    # Contre-pression : attend qu'une place de traitement soit libre pour CE job
    # (`config.PDF_CHUNK_MAX_CONCURRENT_PROCESSING`, défaut 1) avant de répondre.
    # Comme le client n'envoie le morceau suivant qu'après avoir reçu cette
    # réponse, retarder la réponse retarde directement l'envoi du morceau
    # suivant — sans ça, des morceaux s'accumuleraient en traitement simultané
    # sans limite, chacun retenant en mémoire ses pages rasterisées. Hors du
    # `async with job.lock` ci-dessus : la lecture/comptage d'un futur morceau
    # reste possible pendant cette attente, seule la réponse au client est
    # retardée.
    await job.processing_semaphore.acquire()

    task = asyncio.create_task(
        _process_chunk_pages(job.job_id, chunk_bytes, start_page_number, page_count)
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return PDFChunkAckResponse(job_id=job.job_id, pages_received=job.pages_received, status=job.status)