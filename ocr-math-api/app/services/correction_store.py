"""
correction_store.py
Persistance des corrections apportées par l'utilisateur au contenu d'un bloc
transcrit (image croppée sur sa bbox + texte original + texte corrigé +
description de l'erreur), pour constituer un jeu de données exploitable plus
tard (analyse, amélioration du prompt OCR).

Stockage en SQLite (stdlib, aucune dépendance supplémentaire) + fichiers image
sur disque référencés par chemin, cohérent avec le positionnement mono-process
de job_store.py — une contrainte du déploiement (instance unique), pas une
limite d'usage.
"""

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
IMAGES_DIR = DATA_DIR / "images"
DB_PATH = DATA_DIR / "corrections.db"

IMAGES_DIR.mkdir(parents=True, exist_ok=True)


def _execute(query: str, params: tuple = ()) -> None:
    """
    Ouvre une connexion, exécute une requête dans sa propre transaction, la
    referme. Pas de connexion partagée façon _get_client() : sqlite3.Connection
    n'est pas thread-safe, et cette fonction est appelée (via asyncio.to_thread
    côté router) sur des threads potentiellement différents à chaque appel.
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        with conn:
            conn.execute(query, params)
    finally:
        conn.close()


def _init_schema() -> None:
    _execute(
        """
        CREATE TABLE IF NOT EXISTS corrections (
            id TEXT PRIMARY KEY,
            image_path TEXT NOT NULL,
            block_label TEXT,
            confidence INTEGER,
            original_markdown TEXT NOT NULL,
            corrected_markdown TEXT NOT NULL,
            error_description TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )


_init_schema()


def save_correction(
    image_bytes: bytes,
    block_label: str,
    confidence: int,
    original_markdown: str,
    corrected_markdown: str,
    error_description: str,
) -> str:
    """
    Écrit l'image croppée sur disque puis la ligne correspondante en base.
    Retourne l'id généré (uuid4 hex), aussi utilisé comme nom de fichier image
    pour garder les deux corrélés sans jointure ni lookup supplémentaire.
    """
    correction_id = uuid.uuid4().hex
    image_path = IMAGES_DIR / f"{correction_id}.png"
    image_path.write_bytes(image_bytes)

    _execute(
        """
        INSERT INTO corrections (
            id, image_path, block_label, confidence,
            original_markdown, corrected_markdown, error_description, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            correction_id,
            str(image_path),
            block_label,
            confidence,
            original_markdown,
            corrected_markdown,
            error_description,
            datetime.now(timezone.utc).isoformat(),
        ),
    )

    return correction_id
