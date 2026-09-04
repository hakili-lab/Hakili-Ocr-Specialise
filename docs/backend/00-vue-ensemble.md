# Backend — vue d'ensemble

> Dernière vérification : commit `4a30eae`. Projet : `ocr-math-api/`.

## Stack technique

- **Python 3.12**, **FastAPI** (`fastapi==0.141.1`), serveur **Uvicorn**.
- **Anthropic SDK** (`anthropic==0.120.2`, client asynchrone `AsyncAnthropic`)
  pour appeler Claude.
- **Pydantic v2** pour la validation stricte des schémas d'entrée/sortie.
- **Pillow** (redimensionnement d'image, orientation EXIF).
- **PyMuPDF** (`pymupdf`, importé comme `fitz`) pour rasteriser un PDF en
  images.
- **WeasyPrint** pour générer un PDF vectoriel à l'export (nécessite le
  runtime natif GTK3/Pango — voir [`04-service-corrections-export.md`](04-service-corrections-export.md)).
- **SQLite** (stdlib `sqlite3`, aucune dépendance ORM) pour le stockage des
  corrections utilisateur.
- Aucun framework de tests automatisés — voir la section "Tests" plus bas.

## Arborescence

```
ocr-math-api/
├── app/
│   ├── main.py              # Point d'entrée FastAPI, CORS, montage des routers
│   ├── config.py            # Settings (variables d'environnement), @lru_cache
│   ├── security.py          # Dépendance FastAPI verify_api_key (header X-API-Key)
│   ├── routers/
│   │   ├── transcription.py # POST /transcribe, tout le flux PDF (start/status/chunk)
│   │   ├── corrections.py   # POST /corrections
│   │   └── export.py        # POST /export/pdf
│   ├── services/
│   │   ├── claude_service.py     # Appel Anthropic, prompt, retry, sémaphore
│   │   ├── job_store.py          # Jobs PDF en mémoire (suivi de progression)
│   │   ├── correction_store.py   # Persistance SQLite + image des corrections
│   │   └── pdf_export_service.py # Génération PDF via WeasyPrint
│   ├── models/
│   │   └── schemas.py       # Tous les modèles Pydantic (contrat de données)
│   ├── utils/
│   │   ├── image_utils.py   # Validation, redimensionnement, PDF → images
│   │   └── errors.py        # Assainissement des erreurs inattendues
│   └── static/katex/        # Feuille de style KaTeX locale (pour l'export PDF)
├── data/                    # SQLite + images de corrections (créé au runtime, gitignored)
├── tests/
│   └── test_transcribe.py   # Script manuel (pas une suite pytest)
├── requirements.txt         # Dépendances de production, épinglées
├── requirements-dev.txt     # + dépendances de dev (requests, pour le script de test)
├── .env.example
└── Dockerfile
```

## Démarrer le backend en local (sans Docker)

```bash
cd ocr-math-api
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt
cp .env.example .env              # puis renseigner ANTHROPIC_API_KEY, APP_API_KEY
uvicorn app.main:app --reload     # http://127.0.0.1:8000
```

- Documentation interactive (Swagger) : `http://127.0.0.1:8000/docs`.
- **PDF (PyMuPDF)** : assurez-vous que `pymupdf` est installé dans le **même**
  environnement Python que celui qui lance `uvicorn` — vérifiez avec
  `python -c "import fitz"`.
- **Export PDF (WeasyPrint)** : nécessite le runtime GTK3/Pango, absent par
  défaut sur Windows. Sans lui, tout le reste de l'API fonctionne normalement —
  seul `POST /export/pdf` échouera (import différé, voir
  [`04-service-corrections-export.md`](04-service-corrections-export.md)).

## Tests

Il n'y a **pas de suite de tests automatisés** (pas de pytest). `tests/test_transcribe.py`
est un script manuel qui poste `images/Test_01.png` à un serveur local déjà
lancé :

```bash
pip install -r requirements-dev.txt   # ajoute `requests`
python tests/test_transcribe.py
```

**Attention** : ce script est actuellement cassé tel quel — il n'envoie pas le
header `X-API-Key`, donc il reçoit un 401 dès que `APP_API_KEY` est défini côté
serveur. Ajoutez le header manuellement avant de vous y fier.

Aucun linter/formatter n'est configuré côté backend.

## Convention générale de gestion d'erreurs

Systématique dans tous les routers :

| Exception levée par la logique métier | Code HTTP renvoyé |
|---|---|
| `ValueError` | 400 (validation) ou 422 (réponse Claude invalide) selon le contexte |
| `RuntimeError` (ex. clé API manquante) | 500 |
| `anthropic.APIError` | 502, message classé par `describe_anthropic_error()` |
| Tout le reste (`Exception`) | 500, message générique + référence courte |

Le détail du "pourquoi" derrière ce dernier point (jamais de `str(exc)` brut
renvoyé au client) est dans [`../security.md`](../security.md).

## Où aller ensuite

- Le détail de chaque endpoint → [`01-routers.md`](01-routers.md).
- L'appel à Claude (prompt, retry, cache, sémaphore) → [`02-service-claude.md`](02-service-claude.md).
- Le suivi des jobs PDF en mémoire → [`03-service-jobs-pdf.md`](03-service-jobs-pdf.md).
- Les corrections utilisateur et l'export PDF → [`04-service-corrections-export.md`](04-service-corrections-export.md).
- Les schémas Pydantic (le contrat de données avec le frontend) → [`05-models-schemas.md`](05-models-schemas.md).
- Les utilitaires image/PDF et la sécurité → [`06-utils-securite.md`](06-utils-securite.md).
- Toutes les variables d'environnement → [`07-configuration.md`](07-configuration.md).
