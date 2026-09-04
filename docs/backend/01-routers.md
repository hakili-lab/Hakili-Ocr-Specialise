# Routers — tous les endpoints HTTP

> Dernière vérification : commit `4a30eae`. Code : `ocr-math-api/app/routers/*.py`, `ocr-math-api/app/main.py`.

Trois routers, tous montés dans `main.py` (`app.include_router(...)`), tous
protégés par la dépendance FastAPI `verify_api_key` (header `X-API-Key`) sauf
`/` et `/health`. Voir [`06-utils-securite.md`](06-utils-securite.md) pour le
détail de cette vérification.

## `main.py` — point d'entrée

- Crée l'app FastAPI, ajoute le middleware CORS (origines depuis
  `ALLOWED_ORIGINS`, **sans** `allow_credentials` — l'auth est par header,
  jamais par cookie).
- Monte les 3 routers ci-dessous.
- Expose `GET /` et `GET /health` (health check, aucune vérification de clé
  API), utiles pour un load balancer ou un `docker compose ps` qui vérifie
  la santé du conteneur.

> Note cosmétique : le titre/la description FastAPI (`title="OCR Math API"`,
> description mentionnant "mathématiques manuscrites en LaTeX") datent de
> l'ancien positionnement du produit et n'ont pas été mis à jour lors du pivot
> vers les documents administratifs — visible dans Swagger (`/docs`), sans
> impact fonctionnel. Voir [`../decisions-et-limites-connues.md`](../decisions-et-limites-connues.md).

## `transcription.py` — `prefix="/transcribe"`

Le router le plus gros, décrit en détail avec ses diagrammes de séquence dans
[`../architecture/02-flux-image.md`](../architecture/02-flux-image.md),
[`03-flux-pdf.md`](../architecture/03-flux-pdf.md) et
[`04-flux-pdf-chunke.md`](../architecture/04-flux-pdf-chunke.md). Résumé des
endpoints :

| Méthode + chemin | Fonction | Rôle |
|---|---|---|
| `POST /transcribe` | `transcribe_image` | Transcrit une image, réponse synchrone |
| `POST /transcribe/pdf/start` | `start_pdf_transcription` | Ouvre un job PDF (fichier complet), traitement en arrière-plan |
| `GET /transcribe/pdf/status/{job_id}` | `get_pdf_transcription_status` | Interrogé en polling — progression + résultat (partiel ou final) |
| `POST /transcribe/pdf/start-chunked` | `start_chunked_pdf_transcription` | Ouvre un job PDF alimenté par plusieurs morceaux |
| `POST /transcribe/pdf/{job_id}/chunk` | `upload_pdf_chunk` | Reçoit un morceau (sous-PDF), programme son traitement |

Fonctions internes partagées entre les deux flux PDF (non exposées en HTTP) :

- `_process_single_image()` — pipeline commun orientation → resize → base64 →
  Claude, utilisé aussi bien par `transcribe_image` que par
  `_process_page_and_track`.
- `_process_page_and_track()` — traite **une page** et écrit son résultat dans
  `results[page_number]` (jamais par ordre d'arrivée). Incrémente
  `job.pages_done` en effet de bord, dès la fin de cette page précise — c'est
  ce qui permet au polling de refléter une progression réelle sans verrou (une
  seule boucle d'événements `asyncio`, rien ne s'intercale entre lecture et
  écriture).
- `_build_pdf_result()` — construit un `PDFTranscriptionResult` trié par
  numéro de page, réutilisé aussi bien pour le résultat final que pour un
  résultat **partiel** exposé pendant que le job tourne encore.
- `_finalize_pdf_job()` — fabrique `job.result`/`job.status` à partir des
  résultats/erreurs accumulés ; si **aucune** page n'a réussi, `job.status`
  passe à `"error"`.
- `_run_pdf_job()` — orchestre le `asyncio.gather()` sur toutes les pages d'un
  job "legacy" (flux `/pdf/start`).
- `_process_chunk_pages()` — orchestre la rasterisation + le `asyncio.gather()`
  d'**un seul morceau** reçu par le flux chunké.
- `_maybe_finalize_job()` — tente de clore un job chunké à la fin du
  traitement de chaque morceau (voir
  [`../architecture/04-flux-pdf-chunke.md`](../architecture/04-flux-pdf-chunke.md)
  pour la logique de détection de fin).

**Détail à connaître pour toute modification** : `_background_tasks` (un
`set[asyncio.Task]`) garde une référence forte à chaque tâche
"fire-and-forget" créée par `asyncio.create_task`, retirée automatiquement à
la fin via `task.add_done_callback(_background_tasks.discard)`. Sans ça,
`asyncio` ne garde qu'une référence faible aux tâches créées de cette façon et
pourrait les faire ramasser par le garbage collector en plein milieu de leur
exécution — un bug qui ne se manifeste presque jamais en dev (peu de tâches,
courte durée de vie du process) mais peut apparaître en prod sous charge. Ne
supprimez jamais cette ligne en pensant qu'elle est inutile.

## `corrections.py` — `prefix="/corrections"`

Un seul endpoint : `POST /corrections`. Reçoit un multipart (image + champs
texte), valide (`error_description` non vide obligatoire, type d'image
supporté, taille sous `MAX_IMAGE_SIZE_MB`), puis délègue à
`save_correction()` (`correction_store.py`) via `asyncio.to_thread` (SQLite
n'est pas asynchrone). Voir
[`04-service-corrections-export.md`](04-service-corrections-export.md).

## `export.py` — `prefix="/export"`

Un seul endpoint : `POST /export/pdf`. Reçoit `{ html: string }` (le fragment
déjà rendu côté frontend par React + KaTeX), le passe à
`render_export_pdf()` (`pdf_export_service.py`) via `asyncio.to_thread`
(WeasyPrint est bloquant/CPU-bound), renvoie les octets du PDF en
`application/pdf`. Voir
[`04-service-corrections-export.md`](04-service-corrections-export.md).

## Voir aussi

- [`05-models-schemas.md`](05-models-schemas.md) — tous les modèles Pydantic utilisés en entrée/sortie de ces endpoints.
- [`../frontend/06-services-api.md`](../frontend/06-services-api.md) — le code frontend qui appelle chacun de ces endpoints.
- [`../operations-runbook.md`](../operations-runbook.md) — comment diagnostiquer un endpoint qui répond en erreur en production.
