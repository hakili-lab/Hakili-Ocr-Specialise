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
    # Durée (secondes) après laquelle un job PDF terminé (done/error) est
    # purgé du store en mémoire — évite une croissance indéfinie de job_store
    # sur un process qui tourne longtemps. Défaut : 4h.
    JOB_TTL_SECONDS: int = int(os.getenv("JOB_TTL_SECONDS", "14400"))
    # Durée (secondes) sans nouveau morceau reçu au-delà de laquelle un job PDF
    # "chunké" (upload_finalized=False) est considéré bloqué et purgé — sans ça,
    # un client qui abandonne un upload par morceaux en cours de route (onglet
    # fermé, connexion coupée) laisserait un job "processing" indéfiniment
    # impurgeable (job_store._purge_expired_jobs ne balaie aujourd'hui que
    # done/error). Défaut : 30 min.
    JOB_STALL_TIMEOUT_SECONDS: int = int(os.getenv("JOB_STALL_TIMEOUT_SECONDS", "1800"))
    # Défaut aligné sur .env.example (12288) : une valeur trop basse risque de tronquer une
    # réponse verbeuse (page avec un gros tableau) avant la fin du JSON — traité comme un échec
    # (ValueError, voir claude_service.py), pas retenté automatiquement par le retry Anthropic
    # (_create_message_with_retry) puisque ce n'est pas une erreur API. Était monté à 24576
    # (triplé depuis 8192) puis redescendu à 12288 (2026-09-21) : sur le serveur de déploiement
    # (3.7 Gi RAM, sans swap accessible), une réponse Claude plus longue bufferisée en mémoire
    # est un facteur de risque OOM direct — voir docs/decisions-et-limites-connues.md.
    MAX_TOKENS: int = int(os.getenv("MAX_TOKENS", "12288"))
    # Nombre max d'appels Anthropic simultanés (sémaphore global, claude_service.py).
    # Protège contre le rate limit Anthropic (429) et les pics de coût lors du
    # traitement parallèle des pages d'un PDF (_run_pdf_job). Abaissé de 6 à 2
    # (2026-09-21) : sur le serveur de déploiement (3.7 Gi RAM, sans swap), 6
    # pages traitées en parallèle (image rasterisée + réponse Claude en mémoire
    # chacune) a provoqué un OOM-kill du backend (exit 137) pendant un job PDF.
    # Un swap aurait servi de filet de sécurité complémentaire, mais le compte
    # applicatif n'a pas de droits root sur ce serveur pour l'ajouter — en
    # attendant, la concurrence est descendue plus bas que prévu (3 → 2) pour
    # compenser côté application. À remonter dès que le swap est en place —
    # voir docs/decisions-et-limites-connues.md.
    ANTHROPIC_CONCURRENCY: int = int(os.getenv("ANTHROPIC_CONCURRENCY", "2"))
    # Délai max (secondes) accordé à UNE tentative d'appel Anthropic avant de la
    # considérer en échec — sans ça, un appel qui ne répond jamais monopoliserait
    # indéfiniment une place du sémaphore ANTHROPIC_CONCURRENCY.
    ANTHROPIC_REQUEST_TIMEOUT_SECONDS: float = float(os.getenv("ANTHROPIC_REQUEST_TIMEOUT_SECONDS", "120"))
    # Nombre de tentatives supplémentaires (après la première) pour une erreur
    # transitoire (429, 5xx, connexion) — gérées nous-mêmes (claude_service.py,
    # _create_message_with_retry) plutôt que par le retry interne du SDK, pour que
    # l'attente de backoff entre deux tentatives libère la place du sémaphore au
    # lieu de la monopoliser.
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
