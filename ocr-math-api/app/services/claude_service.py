"""
claude_service.py
Logique d'appel à l'API Anthropic pour la transcription OCR de documents administratifs
manuscrits ou imprimés (tableaux, formulaires, relevés, listes).
"""

import asyncio
import json
import logging
import random
import re
from functools import lru_cache

import anthropic
from pydantic import ValidationError

from app.config import get_settings
from app.models.schemas import OCRResult

logger = logging.getLogger(__name__)


@lru_cache
def _get_client() -> anthropic.AsyncAnthropic:
    """
    Client Anthropic asynchrone, réutilisé entre tous les appels (connexions HTTP
    gardées en keep-alive) au lieu d'en recréer un à chaque page transcrite.

    `max_retries=0` désactive le retry automatique du SDK : les tentatives sont
    gérées nous-mêmes dans `_create_message_with_retry`, pour garder le contrôle
    du backoff/logging/classification des erreurs.

    `timeout` borne la durée d'UNE tentative, pour qu'un appel qui ne répond
    jamais ne bloque pas indéfiniment une place du sémaphore.
    """
    settings = get_settings()
    return anthropic.AsyncAnthropic(
        api_key=settings.ANTHROPIC_API_KEY,
        max_retries=0,
        timeout=settings.ANTHROPIC_REQUEST_TIMEOUT_SECONDS,
    )


@lru_cache
def _get_semaphore() -> asyncio.Semaphore:
    """
    Sémaphore global (partagé par tous les appelants, pas un par job PDF)
    limitant le nombre d'appels Anthropic en vol simultanément à
    ANTHROPIC_CONCURRENCY — sans ça, paralléliser les pages d'un PDF
    (_run_pdf_job) enverrait des dizaines d'appels d'un coup et heurterait le
    rate limit Anthropic (429), et deux jobs PDF concurrents (deux utilisateurs)
    cumuleraient leur charge sans limite.

    Les pages sont servies dans l'ordre d'arrivée à la file d'attente (FIFO), sans
    tenir compte du numéro de page : le frontend affiche chaque page dès qu'elle
    est prête, avec son propre numéro (voir `useTranscribe.ts`), donc rien n'exige
    qu'une page de petit numéro passe avant les autres. Seul l'export final
    (PDF/Excel) doit rester trié par numéro de page, ce que `_build_pdf_result`
    (transcription.py) garantit indépendamment de l'ordre de traitement.

    Créé paresseusement (pas au niveau module) pour que l'instance — et les
    primitives de synchronisation qu'elle crée en interne — soit liée à la
    même event loop que celle réellement utilisée au runtime.
    """
    return asyncio.Semaphore(get_settings().ANTHROPIC_CONCURRENCY)


def get_anthropic_semaphore() -> asyncio.Semaphore:
    """
    Accès public à `_get_semaphore()`, pour les appelants hors de ce module qui
    doivent borner leur propre travail à la même limite de concurrence — voir
    `transcription.py`: `_process_single_image`, qui l'acquiert brièvement autour
    du prétraitement d'image (avant l'appel Claude lui-même, qui acquiert la
    même instance en interne) : sans ça, le prétraitement de tout un lot de
    pages démarrerait d'un coup, sans lien avec ANTHROPIC_CONCURRENCY, alors
    que seules `ANTHROPIC_CONCURRENCY` pages peuvent de toute façon être
    envoyées à Claude en même temps.
    """
    return _get_semaphore()


SYSTEM_PROMPT = (
    "Tu es un expert OCR spécialisé dans l'analyse de documents administratifs manuscrits "
    "ou imprimés (tableaux, formulaires, relevés, listes). Produis une transcription "
    "structurée en Markdown.\n\n"
    "RATURES : ignore complètement ratures, gribouillages, taches d'encre, mentions biffées "
    "ou notes illisibles. Ne transcris que le contenu final propre et lisible ; si un passage "
    "est partiellement raturé, transcris la partie visible et baisse la confiance.\n\n"
    "FORMAT : Markdown standard (gras **...**, listes) ; pas de blocs ```markdown, texte brut "
    "uniquement.\n\n"
    "CONTENU INCERTAIN : entoure de ==...== tout contenu dont tu doutes de la lecture exacte, "
    "même un seul caractère — chiffre, lettre, symbole, mot ou nombre entier. N'utilise PAS ce "
    "marqueur pour une rature (ignorée, pas transcrite) ni pour une phrase entière sans raison "
    "précise. Ex : \"Nom : ==Boubacar==\", \"Montant : ==1 250==\".\n\n"
    "TABLEAUX : un bloc par ligne de données, jamais un bloc pour tout le tableau. Chaque "
    "bloc-ligne répète l'en-tête + séparateur (|---|---|) suivis de sa seule ligne de données, "
    "avec son propre id, label (\"Tableau - Ligne N\"), bbox (limitée à cette ligne) et "
    "confidence. N'ajoute PAS de colonne confiance toi-même (ajoutée automatiquement par le "
    "système) — ne transcris que les colonnes réellement présentes.\n\n"
    "ANNOTATION EN COULEUR DIFFÉRENTE : si une cellule porte, en plus du texte d'origine, une "
    "écriture manuscrite dans une couleur nettement différente (souvent rouge vs. texte "
    "d'origine noir/bleu), parfois débordant de la cellule — ce n'est ni une incertitude "
    "(==...==) ni une rature, le texte est lisible et doit être conservé. Ajoute alors une "
    "colonne supplémentaire \"Annotation\" à la fin de CE tableau (cellule vide sur les lignes "
    "sans annotation), et transcris la cellule d'origine SANS cette écriture — ne les mélange "
    "jamais dans la même cellule. N'ajoute cette colonne que si au moins une ligne du tableau "
    "en a besoin.\n\n"
    "Pour chaque bloc : 1) markdown valide ; 2) bbox en PIXELS ABSOLUS entiers (x_min, y_min, "
    "x_max, y_max), origine en haut à gauche, x vers la droite, y vers le bas — dimensions "
    "exactes données dans le message utilisateur, n'estime jamais de fraction toi-même ; "
    "3) confidence 0-100 ; 4) final_warning si des zones sont douteuses.\n\n"
    "Réponds UNIQUEMENT avec un objet JSON valide, sans texte avant ou après, au format :\n"
    "{\n"
    '  "blocks": [\n'
    "    {\n"
    '      "id": 1,\n'
    '      "label": "En-tête du document",\n'
    '      "markdown": "**Fiche de présence** — Classe : ==CM2==, Date : 12/03/2026",\n'
    '      "bbox": { "x_min": 48, "y_min": 40, "x_max": 912, "y_max": 90 },\n'
    '      "confidence": 95\n'
    "    },\n"
    "    {\n"
    '      "id": 2,\n'
    '      "label": "Tableau - Ligne 1",\n'
    '      "markdown": "| Élève | Note | Annotation |\\n|---|---|---|\\n| Awa | 15 | |",\n'
    '      "bbox": { "x_min": 60, "y_min": 400, "x_max": 700, "y_max": 440 },\n'
    '      "confidence": 90\n'
    "    },\n"
    "    {\n"
    '      "id": 3,\n'
    '      "label": "Tableau - Ligne 2",\n'
    '      "markdown": "| Élève | Note | Annotation |\\n|---|---|---|\\n| ==Boubacar== | 12 | Vu, à corriger |",\n'
    '      "bbox": { "x_min": 60, "y_min": 440, "x_max": 700, "y_max": 480 },\n'
    '      "confidence": 70\n'
    "    }\n"
    "  ],\n"
    '  "final_warning": "La zone en bas à droite est floue ; certains montants sont incertains."\n'
    "}"
)

def build_user_prompt(image_width: int, image_height: int) -> str:
    return (
        f"Document administratif manuscrit ou imprimé, {image_width}x{image_height} pixels "
        f"EXACTEMENT — utilise ces dimensions pour les bbox en pixels absolus, n'estime jamais "
        f"de fraction toi-même. Renvoie UNIQUEMENT le JSON demandé, sans balises markdown ni "
        f"explications. Si un tableau a une écriture en couleur différente sur une cellule, "
        f"ajoute-lui une colonne \"Annotation\" dédiée — ne la mélange jamais à la cellule "
        f"d'origine."
    )

def extract_json_from_markdown(text: str) -> str:
    """Nettoie une réponse potentiellement enveloppée dans des balises markdown ```json ... ```."""
    pattern = r"```(?:json)?\s*(.*?)\s*```"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()


def inject_confidence_column(markdown: str, confidence: int) -> str:
    """
    Ajoute une première colonne "Confiance" à un bloc-tableau, à partir du score de
    confiance du bloc lui-même (source de vérité unique — pas laissé à Claude de le
    retranscrire, pour éviter toute incohérence avec la couleur affichée sur la bbox).
    Ne touche pas au markdown si ce n'est pas un tableau (toutes les lignes non vides
    doivent commencer par '|').
    """
    lines = markdown.split("\n")
    content_lines = [line for line in lines if line.strip()]
    if len(content_lines) < 2 or not all(line.strip().startswith("|") for line in content_lines):
        return markdown

    def insert_first_cell(line: str, cell_content: str) -> str:
        rest = line.strip()[1:]
        return f"| {cell_content} |{rest}"

    new_lines = []
    row_index = 0
    for line in lines:
        if not line.strip():
            new_lines.append(line)
            continue
        if row_index == 0:
            new_lines.append(insert_first_cell(line, "Confiance"))
        elif row_index == 1:
            new_lines.append(insert_first_cell(line, "---"))
        else:
            new_lines.append(insert_first_cell(line, f"{confidence}%"))
        row_index += 1

    return "\n".join(new_lines)


def describe_anthropic_error(exc: anthropic.APIError) -> str:
    """
    Message destiné à l'utilisateur final pour une erreur API Anthropic — classé
    par type/`status_code` plutôt que de renvoyer le JSON brut de l'API
    (illisible, non actionnable) ou un message générique qui masquerait la vraie
    cause. Couvre aussi bien les erreurs non-retryables (échouent immédiatement,
    voir `_is_retryable_anthropic_error`) que les erreurs transitoires qui ont
    épuisé leurs tentatives dans `_create_message_with_retry` — dans les deux
    cas, cette fonction est le seul endroit qui traduit l'exception en message
    affiché côté frontend (transcription.py : `transcribe_image` et
    `_process_page_and_track`), donc étendre la classification ici suffit à
    couvrir les deux flux (image seule et pages PDF).
    """
    status_code = getattr(exc, "status_code", None)

    if status_code == 400 and "credit balance" in str(exc).lower():
        return (
            "Le service de transcription est temporairement indisponible : crédits "
            "insuffisants sur le compte Anthropic. Contactez l'administrateur pour "
            "recharger le compte."
        )
    if isinstance(exc, anthropic.AuthenticationError):
        return (
            "Le service de transcription est mal configuré (clé API Anthropic invalide "
            "ou expirée). Contactez l'administrateur."
        )
    if isinstance(exc, anthropic.PermissionDeniedError):
        return "Accès refusé par le service de transcription. Contactez l'administrateur."
    if isinstance(exc, anthropic.RateLimitError):
        return (
            "Le service de transcription est actuellement surchargé (trop de demandes "
            "simultanées). Réessayez dans quelques instants."
        )
    if isinstance(exc, (anthropic.InternalServerError, anthropic.OverloadedError)):
        return (
            "Le service de transcription est temporairement indisponible côté Anthropic. "
            "Réessayez dans quelques instants."
        )
    if isinstance(exc, anthropic.APIConnectionError):
        return (
            "Impossible de contacter le service de transcription (problème réseau ou "
            "délai dépassé). Réessayez."
        )
    if isinstance(exc, anthropic.RequestTooLargeError):
        return "Le document envoyé est trop volumineux pour être traité par le service de transcription."
    if isinstance(exc, anthropic.BadRequestError):
        return f"La requête envoyée au service de transcription est invalide : {exc}"
    # Erreur non classée explicitement (404, 409, 422...) : `str(exc)` reste le
    # dernier recours plutôt qu'un message générique qui masquerait la cause —
    # les erreurs de l'API Anthropic sont déjà des messages lisibles, pas des
    # traces internes.
    return str(exc)


def _is_blank_table_row_block(markdown: str) -> bool:
    """
    Un bloc-ligne de tableau (convention "un bloc par ligne" du prompt : toutes ses
    lignes non vides commencent par '|', même définition que isTableBlockMarkdown côté
    frontend dans tableMarkdown.ts) est considéré vide s'il n'a pas de 3e ligne (la
    ligne de données, après en-tête + séparateur) ou si celle-ci ne contient, une fois
    les '|' retirés, aucun caractère non-blanc (toutes les cellules sont vides).
    Renvoie False pour un bloc non-tableau (n'importe quel autre contenu à 1 ou 2
    lignes ne doit pas être traité comme une ligne de tableau vide).
    """
    lines = [line.strip() for line in markdown.split("\n") if line.strip()]
    if len(lines) < 2 or not all(line.startswith("|") for line in lines):
        return False
    if len(lines) < 3:
        return True
    data_line = lines[2]
    return not data_line.replace("|", "").strip()


def parse_claude_response(text: str, image_width: int, image_height: int) -> OCRResult:
    """
    Extrait le JSON renvoyé par Claude (bbox en pixels absolus), convertit chaque bbox
    en fraction 0-1 par rapport à la taille EXACTE de l'image envoyée (pas la taille
    originale, pas une taille "paddée" par Claude), puis valide via Pydantic.
    """
    cleaned = extract_json_from_markdown(text)

    try:
        raw_data = json.loads(cleaned)

    except json.JSONDecodeError as exc:
        logger.error("Échec du parsing JSON. Réponse brute de Claude : %s", text)
        raise ValueError("La réponse de Claude n'est pas un JSON valide.") from exc

    blocks = raw_data.get("blocks", [])
    kept_blocks = []
    for block in blocks:
        if _is_blank_table_row_block(block.get("markdown", "")):
            logger.warning(
                "Bloc-ligne de tableau vide (id=%s, label=%r) détecté et supprimé "
                "avant envoi au frontend.", block.get("id"), block.get("label"),
            )
            continue
        kept_blocks.append(block)
    raw_data["blocks"] = kept_blocks

    for block in raw_data["blocks"]:
        bbox = block.get("bbox", {})
        for key, denom in (("x_min", image_width), ("x_max", image_width),
                            ("y_min", image_height), ("y_max", image_height)):
            if key in bbox:
                bbox[key] = max(0.0, min(1.0, bbox[key] / denom))

        if "markdown" in block and "confidence" in block:
            block["markdown"] = inject_confidence_column(block["markdown"], block["confidence"])

    try:
        return OCRResult.model_validate(raw_data)
    except ValidationError as exc:
        logger.error("Le JSON de Claude ne respecte pas le schéma attendu : %s", exc)
        raise ValueError(
            "La réponse de Claude ne respecte pas le format attendu (schéma invalide)."
        ) from exc


def _is_retryable_anthropic_error(exc: anthropic.APIError) -> bool:
    """
    Erreurs transitoires qui valent la peine d'être retentées : limite de débit
    (429), erreur serveur Anthropic (5xx, y compris 529 "overloaded"), ou
    problème de connexion/délai dépassé (pas de réponse HTTP du tout — voir
    `_get_client`, `timeout`). Testé sur `status_code` plutôt que sur le nom
    exact de la classe d'exception — plus robuste aux détails de version du
    SDK. Tout le reste (400, 401, 403, 404, 422...) est une erreur de fond
    qu'une nouvelle tentative ne résoudrait pas.
    """
    if isinstance(exc, anthropic.APIConnectionError):
        return True
    status_code = getattr(exc, "status_code", None)
    return status_code == 429 or (status_code is not None and status_code >= 500)


async def _create_message_with_retry(client: anthropic.AsyncAnthropic, **create_kwargs):
    """
    Enveloppe `client.messages.create(...)` d'une boucle de retry/backoff pour les
    erreurs transitoires (voir `_is_retryable_anthropic_error`).

    La place de sémaphore (`_get_semaphore`) est acquise pour UNE tentative à la
    fois, pas pour toute la séquence de retry : relâchée pendant
    `asyncio.sleep(delay)` du backoff entre deux tentatives, pour qu'une autre
    page en attente puisse s'en servir pendant ce temps mort plutôt que de la
    laisser inoccupée.

    Une erreur non-retryable, ou la dernière tentative épuisée, est relevée
    telle quelle : les appelants (`call_anthropic_ocr`, puis
    `_process_page_and_track`/`transcribe_image`) continuent de la traiter
    exactement comme avant — même type d'exception, `describe_anthropic_error`
    inchangé.
    """
    settings = get_settings()
    max_attempts = settings.ANTHROPIC_MAX_RETRIES + 1
    semaphore = _get_semaphore()

    for attempt in range(1, max_attempts + 1):
        # `error_class_name` capture le nom de l'exception AVANT la fin du bloc
        # `except` : Python désaffecte automatiquement la variable liée par
        # `except ... as exc` à la sortie du bloc (`del exc` implicite), donc
        # `exc` lui-même ne peut pas être réutilisé plus bas dans le `logger.warning`.
        error_class_name: str | None = None
        async with semaphore:
            try:
                return await client.messages.create(**create_kwargs)
            except anthropic.APIError as exc:
                if attempt >= max_attempts or not _is_retryable_anthropic_error(exc):
                    raise
                error_class_name = exc.__class__.__name__
        # Backoff exponentiel (1, 2, 4, 8... × la base configurée) + jitter
        # aléatoire, pour éviter que plusieurs pages tombées en 429 en même
        # temps (même compte, même rate limit) ne retentent toutes
        # exactement au même instant. Hors du `async with` : la place est
        # relâchée pendant cette attente, réutilisable par une autre page.
        delay = settings.ANTHROPIC_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
        delay += random.uniform(0, delay * 0.3)
        logger.warning(
            "Appel Anthropic échoué (tentative %d/%d, %s) — nouvelle "
            "tentative dans %.1fs (place de sémaphore relâchée entre-temps).",
            attempt, max_attempts, error_class_name, delay,
        )
        await asyncio.sleep(delay)


async def call_anthropic_ocr(
    image_b64: str,
    media_type: str,
    image_width: int,
    image_height: int,
) -> OCRResult:
    """
    Envoie l'image (déjà en base64, déjà redimensionnée à image_width x image_height)
    à l'API Anthropic et retourne un OCRResult validé.
    Appel asynchrone (AsyncAnthropic) : ne bloque pas l'event loop FastAPI, ce qui permet
    de traiter plusieurs pages en parallèle (asyncio.gather) côté appelant. Le sémaphore
    global (_get_semaphore) borne le nombre d'appels Anthropic réellement en vol à
    ANTHROPIC_CONCURRENCY, quel que soit le nombre de tâches qui attendent ici — servies
    dans l'ordre d'arrivée (FIFO), sans notion de priorité par page : chaque page
    s'affiche côté frontend dès qu'elle est prête, quel que soit son numéro.
    """
    settings = get_settings()
    settings.validate()

    client = _get_client()

    try:
        message = await _create_message_with_retry(
            client,
            model=settings.MODEL,
            max_tokens=settings.MAX_TOKENS,
            # `cache_control` marque ce bloc comme réutilisable côté serveur Anthropic :
            # SYSTEM_PROMPT est identique à chaque appel (rien de variable par page/document
            # n'y figure — les dimensions de l'image sont dans le message utilisateur), donc
            # tant que le cache reste chaud (TTL 5 min, rafraîchi à chaque lecture), les
            # appels suivants d'un même job PDF paient ~90% moins cher cette portion et
            # bénéficient d'un temps de réponse réduit (le modèle n'a pas à la retraiter).
            system=[
                {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
            ],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": build_user_prompt(image_width, image_height),
                        },
                    ],
                }
            ],
        )
    except anthropic.APIError as exc:
        logger.exception("Erreur lors de l'appel à l'API Anthropic (toutes tentatives épuisées ou erreur non-retryable)")
        raise

    if message.stop_reason == "max_tokens":
        logger.error(
            "Réponse Claude tronquée (max_tokens=%d atteint). Augmentez MAX_TOKENS.",
            settings.MAX_TOKENS,
        )
        raise ValueError(
            "La réponse de Claude a été coupée avant la fin (limite de tokens atteinte). "
            "Augmentez MAX_TOKENS dans la configuration."
        )

    response_text = "".join(
        block.text for block in message.content if block.type == "text"
    )

    return parse_claude_response(response_text, image_width, image_height)