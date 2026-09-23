"""
config.py
Chargement centralisé de la configuration de l'application via variables d'environnement.
"""

import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


class Settings:
    """Configuration globale de l'application, lue depuis les variables d'environnement."""

    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    # Clé partagée frontend → backend (distincte d'ANTHROPIC_API_KEY, qui est
    # backend → Anthropic) : voir app/security.py pour la dépendance qui la vérifie.
    APP_API_KEY: str = os.getenv("APP_API_KEY", "")
    MODEL: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")
    MAX_IMAGE_SIZE_MB: float = float(os.getenv("MAX_IMAGE_SIZE_MB", "5"))
    # PDF : limite dédiée plutôt que dérivée de MAX_IMAGE_SIZE_MB, car un PDF
    # long/haute résolution n'a pas la même échelle qu'une image unique.
    MAX_PDF_SIZE_MB: float = float(os.getenv("MAX_PDF_SIZE_MB", "500"))
    MAX_PDF_PAGES: int = int(os.getenv("MAX_PDF_PAGES", "600"))
    # Durée (secondes) après laquelle un job PDF terminé (done/error) est purgé
    # du store en mémoire, pour éviter une croissance indéfinie de job_store.
    # Mesurée depuis `job.updated_at` (rafraîchi à la finalisation du job, voir
    # `_finalize_pdf_job` dans transcription.py), jamais depuis `job.created_at` :
    # ancrer sur la création purgerait un job dont le traitement a duré plus
    # longtemps que ce délai dès l'instant où il se termine, potentiellement
    # avant même le dernier poll du frontend. Chaque `PageResult` conservé
    # embarque l'image complète en base64 ; le frontend n'a plus besoin de la
    # copie backend une fois son dernier poll reçu (`refetchInterval` s'arrête
    # dès que `status !== "processing"`, voir `useTranscribe.ts`).
    JOB_TTL_SECONDS: int = int(os.getenv("JOB_TTL_SECONDS", "300"))
    # Intervalle (secondes) de la tâche de fond qui balaie `job_store` pour
    # purger les jobs expirés (voir job_store.purge_loop, démarrée dans
    # main.py) — en plus du balayage opportuniste existant à chaque création
    # de job, pour que la purge ait lieu même en période sans nouveau job
    # (sinon un job "done" resterait en mémoire indéfiniment si personne
    # n'en démarre un nouveau après lui).
    JOB_PURGE_INTERVAL_SECONDS: int = int(os.getenv("JOB_PURGE_INTERVAL_SECONDS", "60"))
    # Durée (secondes) sans nouveau morceau reçu au-delà de laquelle un job PDF
    # "chunké" (upload_finalized=False) est considéré bloqué et purgé — sans ça,
    # un client qui abandonne un upload par morceaux en cours de route (onglet
    # fermé, connexion coupée) laisserait un job "processing" indéfiniment
    # impurgeable (job_store._purge_expired_jobs ne balaie aujourd'hui que
    # done/error). Défaut : 30 min.
    JOB_STALL_TIMEOUT_SECONDS: int = int(os.getenv("JOB_STALL_TIMEOUT_SECONDS", "1800"))
    # Nombre de morceaux d'un même job PDF chunké autorisés à rasteriser/OCRiser en
    # parallèle (voir job_store.PDFJob.processing_semaphore) — borne l'upload du
    # morceau suivant : POST /pdf/{job_id}/chunk ne répond qu'une fois une place
    # libre, donc le client (envoi strictement séquentiel) n'envoie le morceau
    # suivant qu'à ce moment-là. Un morceau (des dizaines de pages) dépasse déjà
    # largement ANTHROPIC_CONCURRENCY en pages ; en admettre un second en parallèle
    # n'apporte donc quasiment aucun débit supplémentaire, seulement de la mémoire
    # de rasterisation en plus — défaut à 1 (traitement des morceaux strictement
    # séquentiel) pour la borne mémoire la plus stricte.
    PDF_CHUNK_MAX_CONCURRENT_PROCESSING: int = int(os.getenv("PDF_CHUNK_MAX_CONCURRENT_PROCESSING", "1"))
    # Une valeur trop basse risque de tronquer une réponse verbeuse (page avec un gros
    # tableau) avant la fin du JSON — traité comme un échec (ValueError, voir
    # claude_service.py), jamais retenté automatiquement par le retry Anthropic
    # (_create_message_with_retry) puisque ce n'est pas une erreur API. Une valeur trop
    # haute augmente d'autant la mémoire bufferisée par réponse en cours de traitement
    # parallèle (voir ANTHROPIC_CONCURRENCY) : les deux paramètres doivent être calibrés
    # ensemble par rapport à la RAM disponible sur le serveur de déploiement.
    MAX_TOKENS: int = int(os.getenv("MAX_TOKENS", "24576"))
    # Nombre max d'appels Anthropic simultanés (asyncio.Semaphore standard, FIFO — voir
    # claude_service.py: _get_semaphore). Protège contre le rate limit Anthropic (429) et
    # borne le pic mémoire/coût lors du traitement parallèle des pages d'un PDF
    # (_run_pdf_job) : chaque appel en vol retient en mémoire l'image envoyée et la
    # réponse Claude en cours de réception, donc ce paramètre est aussi le principal
    # levier pour rester sous la RAM disponible du serveur de déploiement — voir
    # docs/decisions-et-limites-connues.md pour la valeur validée en production.
    ANTHROPIC_CONCURRENCY: int = int(os.getenv("ANTHROPIC_CONCURRENCY", "3"))
    # Délai max (secondes) accordé à UNE tentative d'appel Anthropic avant de la
    # considérer en échec — sans ça, un appel qui ne répond jamais monopoliserait
    # indéfiniment une place du sémaphore ANTHROPIC_CONCURRENCY.
    ANTHROPIC_REQUEST_TIMEOUT_SECONDS: float = float(os.getenv("ANTHROPIC_REQUEST_TIMEOUT_SECONDS", "120"))
    # Nombre de tentatives supplémentaires (après la première) pour une erreur
    # transitoire (429, 5xx, connexion) — gérées nous-mêmes (claude_service.py,
    # _create_message_with_retry) plutôt que par le retry interne du SDK, pour
    # garder le contrôle du backoff/logging/classification. La place de sémaphore
    # est relâchée pendant l'attente de backoff entre deux tentatives : les pages
    # sont servies dans l'ordre d'arrivée (FIFO), donc une autre page en attente
    # peut s'en servir pendant ce temps mort.
    ANTHROPIC_MAX_RETRIES: int = int(os.getenv("ANTHROPIC_MAX_RETRIES", "3"))
    # Délai de base (secondes) du backoff exponentiel entre deux tentatives —
    # doublé à chaque tentative (1, 2, 4, 8...), plus un peu d'aléatoire (jitter)
    # pour éviter que plusieurs pages en 429 en même temps ne retentent toutes au
    # même instant.
    ANTHROPIC_RETRY_BASE_DELAY_SECONDS: float = float(os.getenv("ANTHROPIC_RETRY_BASE_DELAY_SECONDS", "1"))
    # Origines autorisées par CORS, séparées par des virgules. Par défaut les
    # ports Vite en dev local ; à surcharger en déploiement (ex. l'origine du
    # frontend dockerisé) via la variable d'environnement ALLOWED_ORIGINS.
    ALLOWED_ORIGINS: list[str] = [
        origin.strip()
        for origin in os.getenv(
            "ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:5174,http://localhost:8021"
        ).split(",")
        if origin.strip()
    ]

    def validate(self) -> None:
        """Vérifie que les variables critiques sont bien définies."""
        if not self.ANTHROPIC_API_KEY:
            raise RuntimeError(
                "Clé API Anthropic manquante. Définissez la variable d'environnement "
                "ANTHROPIC_API_KEY (fichier .env ou variable système)."
            )


@lru_cache
def get_settings() -> Settings:
    """Retourne une instance mise en cache des settings (évite de relire l'env à chaque appel)."""
    settings = Settings()
    return settings
