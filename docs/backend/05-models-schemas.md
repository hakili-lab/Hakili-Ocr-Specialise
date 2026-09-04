# Schémas Pydantic — `models/schemas.py`

> Dernière vérification : commit `4a30eae`. Code : `ocr-math-api/app/models/schemas.py`.

Ce fichier est **le contrat de données** entre le backend et le frontend.
Chaque modèle a un miroir manuel côté TypeScript dans
`hakili-ocr/src/types/index.ts` (voir
[`../frontend/01-etat-global.md`](../frontend/01-etat-global.md)) — **il n'y a
aucune génération automatique entre les deux** : si vous changez un schéma ici,
vous devez répercuter le changement à la main côté frontend, sinon les deux
projets se désynchronisent silencieusement (TypeScript ne verra pas l'erreur,
elle n'apparaîtra qu'au runtime).

## `BoundingBox`

Coordonnées normalisées `[0, 1]` d'un bloc sur l'image (jamais des pixels —
la conversion pixels → fraction se fait dans `claude_service.py`, voir
[`02-service-claude.md`](02-service-claude.md)).

- `x_min`, `y_min`, `x_max`, `y_max` : chacun `Field(..., ge=0, le=1)`.
- **Validateur `clamp_to_unit_range`** (`mode="before"`) : force toute valeur
  hors `[0, 1]` à rester dans cette plage plutôt que de rejeter — tolère une
  petite erreur d'arrondi du modèle plutôt que de faire échouer toute la
  transcription pour un dépassement de quelques millièmes.
- **Validateurs `x_max_must_exceed_x_min`/`y_max_must_exceed_y_min`** : lèvent
  une erreur de validation si la boîte est dégénérée (max < min).

## `Block`

Un bloc de contenu identifié par Claude (un paragraphe simple, ou une ligne de
tableau — voir la convention "un bloc par ligne" dans
[`02-service-claude.md`](02-service-claude.md)).

- `id: int`, `label: str`, `markdown: str`, `bbox: BoundingBox`,
  `confidence: int` (`ge=0, le=100`).

## `OCRResult`

Résultat pour **une seule page/image** : `blocks: list[Block]` +
`final_warning: Optional[str]` (message global optionnel, ex. "certaines zones
sont floues").

## `PageResult`

Une page transcrite **avec son image** : `page_number`, `image_b64`,
`media_type`, `width`, `height`, `ocr: OCRResult`. L'image est réencodée en
base64 et renvoyée inline (pas d'URL séparée) — c'est ce que le frontend
affiche comme `imagePreviewUrl` pour chaque page d'un PDF.

## `PDFTranscriptionResult`

Résultat complet d'un document multi-pages : `pages: list[PageResult]` +
`final_warning: Optional[str]` (warnings agrégés de toutes les pages, joints
par des retours à la ligne — voir `_build_pdf_result` dans
[`01-routers.md`](01-routers.md)).

## `TranscriptionResponse`

Enveloppe de réponse de `POST /transcribe` : `success: bool = True` +
`result: OCRResult`.

## `ErrorResponse`

Format standard documenté pour les réponses d'erreur (utilisé dans les
`responses={...}` de chaque endpoint pour Swagger) : `success: bool = False`,
`error: str`, `detail: Optional[str]`. **Note** : en pratique, le corps
d'erreur réel renvoyé par FastAPI via `HTTPException(detail=...)` est
`{ "detail": "..." }`, pas exactement cette forme — ce schéma sert surtout à
documenter l'intention dans Swagger.

## `PDFJobStartResponse`

Réponse immédiate à l'ouverture d'un job PDF (les deux flux, classique et
chunké, réutilisent ce même schéma) : `job_id: str`, `pages_total: int`.

## `PDFJobStatusResponse`

Réponse du polling de statut : `job_id`, `status` (`JobStatus`), `pages_done`,
`pages_total`, `result: Optional[PDFTranscriptionResult]`, `error: Optional[str]`.

**Point crucial documenté directement dans le docstring du modèle** : `result`
peut être non-null **avant** que `status` passe à `"done"` — dès qu'au moins
une page est prête, un résultat partiel est exposé. **`status` reste la seule
source de vérité** pour savoir si le document entier est terminé ; ne jamais
déduire "terminé" de la simple présence de `result`. Voir
[`../architecture/03-flux-pdf.md`](../architecture/03-flux-pdf.md).

## `PDFChunkedStartRequest`

Corps de `POST /pdf/start-chunked` : `pages_expected: int` (`gt=0`) — une
estimation annoncée par le client, plafonnée à `MAX_PDF_PAGES` côté serveur
(`create_chunked_job`) avant de devenir le budget cumulé du job. **Ne jamais
faire confiance à cette valeur pour autre chose que cette estimation initiale**
— voir [`03-service-jobs-pdf.md`](03-service-jobs-pdf.md).

## `PDFChunkAckResponse`

Réponse à chaque `POST /pdf/{job_id}/chunk` : `job_id`, `pages_received`,
`status`. **Un simple accusé de réception**, pas le résultat de la
transcription — celui-ci n'arrive que via `GET /pdf/status/{job_id}` une fois
le morceau traité.

## `CorrectionResponse`

`{ id: str }` — l'id (uuid4 hex) de la correction enregistrée.

## `ExportPdfRequest`

Corps de `POST /export/pdf` : `{ html: str }` (`min_length=1`) — le fragment
HTML déjà rendu côté frontend, voir
[`04-service-corrections-export.md`](04-service-corrections-export.md).

## Type partagé `JobStatus`

```python
JobStatus = Literal["processing", "done", "error"]
```

Partagé entre `job_store.PDFJob.status` et `PDFJobStatusResponse.status` —
toujours ces trois valeurs exactes, jamais d'état intermédiaire.

## Voir aussi

- [`../frontend/01-etat-global.md`](../frontend/01-etat-global.md) — le miroir TypeScript (`types/index.ts`), à maintenir manuellement synchronisé.
- [`02-service-claude.md`](02-service-claude.md) — d'où viennent les données brutes validées par `OCRResult`.
- [`01-routers.md`](01-routers.md) — quel endpoint utilise quel schéma.
