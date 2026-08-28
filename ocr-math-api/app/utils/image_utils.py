"""
image_utils.py
Fonctions utilitaires pour la validation et l'encodage des images uploadées.
"""

import base64
import io
import logging
import math

from fastapi import UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)
SUPPORTED_MEDIA_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
}
_PIL_FORMAT_BY_MEDIA_TYPE = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
}

VISION_MAX_EDGE = 1568
VISION_MAX_TOKENS = 1568


def validate_content_type(content_type: str) -> str:
    """Vérifie que le type MIME est supporté et le normalise (jpg → jpeg) vers la forme attendue par Claude."""
    normalized = content_type.lower()
    if normalized not in SUPPORTED_MEDIA_TYPES:
        raise ValueError(
            f"Type de fichier non supporté : '{content_type}'. "
            "Formats acceptés : PNG, JPEG."
        )
    return "image/jpeg" if SUPPORTED_MEDIA_TYPES[normalized] == "jpeg" else "image/png"


_READ_CHUNK_SIZE = 1024 * 1024  # 1 Mo


async def read_upload_with_byte_limit(file: UploadFile, max_bytes: int, label: str = "Fichier") -> bytes:
    """
    Cœur commun à `read_upload_with_limit` (budget en Mo, un seul appel — image ou PDF envoyé
    d'un coup) et à la lecture d'un morceau d'upload PDF fractionné (budget en octets, cumulé sur
    plusieurs requêtes liées à un même job — voir `upload_pdf_chunk` dans
    `routers/transcription.py`, où `max_bytes` est le budget *restant* du job, pas une constante
    fixe). Lit `file` par blocs de 1 Mo et lève une `ValueError` dès que la taille cumulée dépasse
    `max_bytes`, sans bufferiser le reste du corps. Contrairement à `await file.read()` suivi
    d'une vérification de taille après coup, un client qui envoie un fichier arbitrairement gros
    ne peut pas faire exploser la mémoire du serveur avant que la limite soit contrôlée.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_READ_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(
                f"{label} trop lourd (> {max_bytes / (1024 * 1024):.0f} Mo). "
                "Compressez le fichier avant de réessayer."
            )
        chunks.append(chunk)
    return b"".join(chunks)


async def read_upload_with_limit(file: UploadFile, max_mb: float, label: str = "Fichier") -> bytes:
    """Variante à budget unique en Mo (un seul appel HTTP) de `read_upload_with_byte_limit` — voir son docstring."""
    return await read_upload_with_byte_limit(file, int(max_mb * 1024 * 1024), label)


def normalize_orientation(raw_bytes: bytes, media_type: str) -> bytes:
    """
    Réencode l'image avec l'orientation EXIF appliquée physiquement aux pixels
    (`ImageOps.exif_transpose`), pour que la bbox retournée par Claude reste
    cohérente avec ce que l'utilisateur voit réellement — sans ça, une photo
    prise en paysage mais taguée "portrait" par le téléphone serait envoyée à
    Claude dans la mauvaise orientation. Retourne les bytes bruts sans y
    toucher si l'image n'a pas d'orientation EXIF non standard, ou si elle ne
    peut pas être décodée (log un warning plutôt que de faire échouer toute
    la requête pour un problème de métadonnées).
    """
    try:
        image = Image.open(io.BytesIO(raw_bytes))
        image.load()
    except UnidentifiedImageError:
        logger.warning("Impossible de décoder l'image pour normalisation EXIF ; envoi des bytes bruts.")
        return raw_bytes

    exif = image.getexif()
    orientation_tag = 0x0112
    if orientation_tag not in exif or exif[orientation_tag] == 1:
        return raw_bytes

    transposed = ImageOps.exif_transpose(image)
    pil_format = _PIL_FORMAT_BY_MEDIA_TYPE.get(media_type, image.format or "JPEG")
    buffer = io.BytesIO()
    save_kwargs = {"quality": 95} if pil_format == "JPEG" else {}
    if pil_format == "JPEG" and transposed.mode != "RGB":
        transposed = transposed.convert("RGB")
    transposed.save(buffer, format=pil_format, **save_kwargs)
    return buffer.getvalue()


def encode_bytes_to_base64(raw_bytes: bytes) -> str:
    return base64.b64encode(raw_bytes).decode("utf-8")


def _count_image_tokens(width: int, height: int) -> int:
    return math.ceil(width / 28) * math.ceil(height / 28)


def resized_size(width: int, height: int, max_edge: int = VISION_MAX_EDGE,
                  max_tokens: int = VISION_MAX_TOKENS) -> tuple[int, int]:
    """
    Calcule les dimensions les plus grandes possibles qui respectent à la fois
    la limite de bord (`max_edge`, arrondi au multiple de 28 supérieur — la
    taille de tuile utilisée par le calcul de tokens vision de Claude) et le
    budget de tokens (`max_tokens`, ~ largeur/28 arrondi × hauteur/28 arrondi).
    Retourne `(width, height)` tel quel si l'image tient déjà dans les deux
    limites. Sinon, recherche dichotomique sur la plus grande dimension qui
    satisfait `fits(...)` à ratio d'aspect constant — préféré à un simple
    ratio de mise à l'échelle car `fits` n'est pas linéaire (l'arrondi au
    multiple de 28 rend la fonction "en escalier", pas une simple proportion).
    """
    def fits(w: int, h: int) -> bool:
        return (
            math.ceil(w / 28) * 28 <= max_edge
            and math.ceil(h / 28) * 28 <= max_edge
            and _count_image_tokens(w, h) <= max_tokens
        )

    if fits(width, height):
        return (width, height)
    if height > width:
        resized_h, resized_w = resized_size(height, width, max_edge, max_tokens)
        return (resized_w, resized_h)

    aspect_ratio = width / height
    lo, hi = 1, width
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if fits(mid, max(round(mid / aspect_ratio), 1)):
            lo = mid
        else:
            hi = mid
    return (lo, max(round(lo / aspect_ratio), 1))


def resize_for_vision(raw_bytes: bytes, media_type: str) -> tuple[bytes, int, int]:
    """
    Redimensionne l'image aux dimensions calculées par `resized_size` si
    nécessaire (sinon la renvoie inchangée). Retourne aussi la largeur/hauteur
    finales : elles sont réutilisées telles quelles pour normaliser les bbox
    pixel renvoyées par Claude en fractions [0,1] — la cohérence entre "ce qui
    est envoyé à Claude" et "ce qui sert de référentiel de normalisation" est
    ce qui garde les bbox alignées avec l'image affichée côté frontend.
    """
    image = Image.open(io.BytesIO(raw_bytes))
    image.load()
    width, height = image.size

    target_w, target_h = resized_size(width, height)
    if (target_w, target_h) == (width, height):
        return raw_bytes, width, height

    resized = image.resize((target_w, target_h), Image.LANCZOS)
    pil_format = _PIL_FORMAT_BY_MEDIA_TYPE.get(media_type, image.format or "JPEG")
    if pil_format == "JPEG" and resized.mode != "RGB":
        resized = resized.convert("RGB")
    buffer = io.BytesIO()
    save_kwargs = {"quality": 95} if pil_format == "JPEG" else {}
    resized.save(buffer, format=pil_format, **save_kwargs)
    return buffer.getvalue(), target_w, target_h


# ─── PDF → Images ───────────────────────────────────────────────

def count_pdf_pages(raw_bytes: bytes, max_pages: int | None = None) -> int:
    """
    Compte les pages d'un PDF sans rendre le moindre pixel — ouvre juste la
    structure du document (table des pages), ce qui est nettement moins coûteux
    que `convert_pdf_to_images`. Utilisé pour valider un budget de pages
    (upload par morceaux, voir `routers/transcription.py`) avant de décider de
    rasteriser, sans payer le coût du rendu pour cette seule validation. Lève la
    même `ValueError` que `convert_pdf_to_images` si `max_pages` est dépassé.
    """
    import fitz  # PyMuPDF — import différé, voir convert_pdf_to_images

    doc = fitz.open(stream=raw_bytes, filetype="pdf")
    page_count = len(doc)
    doc.close()

    if max_pages is not None and page_count > max_pages:
        raise ValueError(
            f"PDF trop long ({page_count} pages). Limite acceptée : {max_pages} pages."
        )
    return page_count


def convert_pdf_to_images(
    raw_bytes: bytes, dpi: int = 150, max_pages: int | None = None
) -> list[tuple[bytes, int, int]]:
    """
    Convertit un PDF en liste d'images (bytes, width, height).
    Chaque page est rendue en PNG à la résolution donnée.

    Si `max_pages` est fourni et que le PDF en contient davantage, lève une
    `ValueError` avant de rendre la moindre page — un PDF bien en dessous de
    la limite de taille peut quand même contenir des milliers de pages,
    déclenchant chacune un rendu PyMuPDF et un appel Claude payant.

    Rendu séquentiel : PyMuPDF ne libère pas le GIL pendant `get_pixmap`, donc
    un `ThreadPoolExecutor` n'apporte quasi rien (~10 %, mesuré) — un vrai
    parallélisme demanderait un pool de *processus* (~3×), au prix du coût
    d'IPC (le PDF sérialisé vers chaque worker) et de la gestion du cycle de
    vie du pool. Volontairement pas fait tant que ce n'est pas nécessaire.
    """
    import fitz  # PyMuPDF — import différé pour ne pas rendre le démarrage du serveur dépendant de sa présence

    doc = fitz.open(stream=raw_bytes, filetype="pdf")
    if max_pages is not None and len(doc) > max_pages:
        page_count = len(doc)
        doc.close()
        raise ValueError(
            f"PDF trop long ({page_count} pages). Limite acceptée : {max_pages} pages."
        )

    pages: list[tuple[bytes, int, int]] = []

    for page_num in range(len(doc)):
        page = doc.load_page(page_num)
        # Render à la résolution demandée
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat)
        img_bytes = pix.tobytes("png")
        pages.append((img_bytes, pix.width, pix.height))

    doc.close()
    return pages