# Configuration backend — `config.py` et variables d'environnement

> Dernière vérification : commit `4a30eae`. Code : `ocr-math-api/app/config.py`. Fichier source des valeurs par défaut : `ocr-math-api/.env.example`.

Toutes les variables sont chargées une seule fois via `@lru_cache` (`get_settings()`),
depuis l'environnement (fichier `.env` via `python-dotenv`, ou variables système
— en Docker, injectées par `env_file: .env` dans `docker-compose.yml`).

## Référence complète

| Variable | Défaut | Rôle |
|---|---|---|
| `ANTHROPIC_API_KEY` | *(vide — obligatoire)* | Clé API Anthropic. Sans elle, `settings.validate()` lève `RuntimeError` au premier appel à Claude (pas au démarrage du serveur). |
| `APP_API_KEY` | *(vide — obligatoire en pratique)* | Clé partagée frontend → backend (header `X-API-Key`). Si vide, **toutes** les requêtes protégées sont refusées (voir [`06-utils-securite.md`](06-utils-securite.md)). Distincte d'`ANTHROPIC_API_KEY`. |
| `ANTHROPIC_MODEL` | `claude-sonnet-5` | Modèle Claude utilisé pour l'OCR. |
| `MAX_IMAGE_SIZE_MB` | `5` | Taille max d'une image uploadée (`/transcribe`, `/corrections`). |
| `MAX_PDF_SIZE_MB` | `500` | Taille max d'un PDF — limite **dédiée**, pas dérivée de `MAX_IMAGE_SIZE_MB` (un PDF long/haute résolution n'a pas la même échelle qu'une image unique). |
| `MAX_PDF_PAGES` | `600` | Nombre max de pages d'un PDF — rejeté avant la moindre rasterisation. |
| `JOB_TTL_SECONDS` | `14400` (4h) | Durée après laquelle un job PDF terminé (`done`/`error`) est purgé de la mémoire. |
| `JOB_STALL_TIMEOUT_SECONDS` | `1800` (30 min) | Durée sans nouveau morceau reçu au-delà de laquelle un job chunké non finalisé est considéré bloqué et purgé. |
| `MAX_TOKENS` | `8192` | Budget de tokens de sortie pour un appel Claude. Trop bas → réponse tronquée sur un document avec un gros tableau (traité comme une erreur, pas retenté). |
| `ANTHROPIC_CONCURRENCY` | `6` (test local ; `2` en production, voir [`../decisions-et-limites-connues.md`](../decisions-et-limites-connues.md)) | Nombre max d'appels Anthropic simultanés (sémaphore **à priorité** — voir [`02-service-claude.md`](02-service-claude.md)) — protège contre le rate limit Anthropic et les pics de coût. |
| `ANTHROPIC_REQUEST_TIMEOUT_SECONDS` | `120` | Délai max accordé à **une** tentative d'appel Anthropic. |
| `ANTHROPIC_MAX_RETRIES` | `3` | Tentatives **supplémentaires** (après la première) pour une erreur transitoire (429, 5xx, connexion). |
| `ANTHROPIC_RETRY_BASE_DELAY_SECONDS` | `1` | Délai de base du backoff exponentiel entre deux tentatives (doublé à chaque tentative + jitter). |
| `ALLOWED_ORIGINS` | `http://localhost:5173,http://localhost:5174,http://localhost:8021` | Origines autorisées par CORS, séparées par des virgules. |

## Points d'attention en cas de modification

- **`ANTHROPIC_CONCURRENCY`** — l'augmenter sans discernement peut heurter le
  rate limit réel du compte Anthropic (429 en rafale) ; le retry/backoff
  (`02-service-claude.md`) absorbe des 429 occasionnels, pas un mur
  systématique. Depuis le 2026-09-21, une page mid-retry tient sa place
  pendant tout le backoff (pas relâchée entre deux tentatives) — un pic de
  429 réduit donc temporairement le débit utile plus qu'avant, en échange
  d'un ordre de file d'attente plus prévisible (`PrioritySemaphore`).
- **`MAX_TOKENS`** — l'augmenter sans certitude que le modèle en a besoin
  augmente le coût par appel (les tokens de sortie sont facturés, pas
  seulement consommés) sans bénéfice si les réponses ne sont jamais tronquées
  en pratique. Un signal de troncature (`stop_reason == "max_tokens"`) est
  logué en erreur — surveillez les logs avant d'augmenter à l'aveugle.
- **`ALLOWED_ORIGINS`** — un décalage entre cette variable et l'origine
  réellement publiée du frontend fait échouer **silencieusement** toutes les
  requêtes côté navigateur (erreur CORS visible uniquement dans la console
  navigateur, jamais dans les logs backend — la requête n'atteint même pas le
  handler). **Déjà arrivé une fois sur ce projet** (voir le commentaire dans
  le `.env.example` racine) — toujours vérifier cette variable en premier
  quand "le frontend n'arrive pas à joindre le backend" en déploiement.
- **`JOB_TTL_SECONDS`/`JOB_STALL_TIMEOUT_SECONDS`** — les baisser trop
  agressivement risque de purger un job PDF encore légitimement en cours sur
  un document très long ; les monter trop haut retarde la libération mémoire
  d'un job abandonné.

## `Settings.validate()`

```python
def validate(self) -> None:
    if not self.ANTHROPIC_API_KEY:
        raise RuntimeError("Clé API Anthropic manquante. ...")
```

Appelée **au moment du premier appel à Claude** (`call_anthropic_ocr`), pas au
démarrage du serveur — le serveur démarre normalement même sans
`ANTHROPIC_API_KEY` configurée ; l'erreur n'apparaît qu'à la première tentative
de transcription (HTTP 500, message explicite).

## Fichiers `.env`

| Fichier | Usage |
|---|---|
| `ocr-math-api/.env.example` | Modèle pour `ocr-math-api/.env`, utilisé en développement local (hors Docker). |
| `.env.example` (racine du dépôt) | Modèle pour `.env` (racine), lu par `docker-compose.yml` — regroupe les variables backend **et** frontend en un seul fichier pour le déploiement Docker. |

**Ne jamais committer un `.env` réel** — déjà exclu via `.gitignore` (racine).

## Voir aussi

- [`../deployment/01-docker-compose.md`](../deployment/01-docker-compose.md) — comment ces variables sont injectées en Docker (build args vs runtime).
- [`06-utils-securite.md`](06-utils-securite.md) — `APP_API_KEY` en détail.
- [`02-service-claude.md`](02-service-claude.md) — toutes les variables `ANTHROPIC_*` en contexte.
