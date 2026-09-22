# Service Claude — `claude_service.py`

> Dernière vérification : commit `4a30eae`. Code : `ocr-math-api/app/services/claude_service.py`.

Ce fichier est le **seul point de contact** entre le backend et l'API
Anthropic. Toute la logique de prompt, de fiabilité réseau (retry) et de
protection contre la surcharge (sémaphore) vit ici.

## Le client Anthropic (`_get_client`)

```python
@lru_cache
def _get_client() -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(
        api_key=settings.ANTHROPIC_API_KEY,
        max_retries=0,
        timeout=settings.ANTHROPIC_REQUEST_TIMEOUT_SECONDS,
    )
```

- **Un seul client, réutilisé** (`@lru_cache`) — connexions HTTP en
  keep-alive, pas de reconnexion à chaque page transcrite.
- **`max_retries=0`** : désactive volontairement le retry automatique du SDK.
  Les tentatives sont gérées à la main par `_create_message_with_retry()`
  (voir plus bas) — la raison précise est expliquée dans cette même section.
- **`timeout`** borne la durée d'**une** tentative (`ANTHROPIC_REQUEST_TIMEOUT_SECONDS`,
  défaut 120s), pour qu'un appel qui ne répond jamais ne bloque pas
  indéfiniment une place du sémaphore.

## Le sémaphore de concurrence à priorité (`_get_semaphore`, `PrioritySemaphore`)

```python
@lru_cache
def _get_semaphore() -> PrioritySemaphore:
    return PrioritySemaphore(get_settings().ANTHROPIC_CONCURRENCY)
```

**Un seul sémaphore, global à tout le process** — pas un par job PDF. Borne à
`ANTHROPIC_CONCURRENCY` (défaut 3 — valeur de test local ; 2 en production, voir
[`../decisions-et-limites-connues.md`](../decisions-et-limites-connues.md)) le
nombre d'appels Anthropic **réellement en vol** au même instant, que les appels
viennent des pages parallélisées d'un même PDF (`_run_pdf_job`) ou de plusieurs
utilisateurs différents en même temps. Sans lui : paralléliser les pages d'un
PDF enverrait des dizaines d'appels d'un coup et heurterait le rate limit
Anthropic (429), et deux jobs PDF simultanés cumuleraient leur charge sans
aucune limite.

**`PrioritySemaphore` plutôt qu'`asyncio.Semaphore`** (depuis le 2026-09-21) :
classe maison (`claude_service.py`) basée sur un tas min (`heapq`) au lieu
d'une simple `deque` FIFO. Quand une place se libère et que plusieurs tâches
attendent, celle avec le plus petit `priority` (le plus petit numéro de page)
est servie en premier, pas forcément la première arrivée. But : le frontend
(`takeReadyPagePrefix`) n'affiche que le préfixe contigu de pages prêtes à
partir de la page 1 — sans priorité, une page à faible numéro repoussée loin
dans la file (ex. après une erreur retryable) retardait l'affichage de toutes
les pages suivantes déjà transcrites. Chaque appelant passe sa priorité via
`call_anthropic_ocr(..., priority=...)` → `_create_message_with_retry` →
`_acquire_priority_slot` ; `_process_page_and_track` y passe le numéro de
page, `transcribe_image` (image seule) laisse le défaut `0`.

**Limite assumée** : ceci ne réordonne que les tâches **en attente** au
moment où une place se libère — ça ne garantit pas qu'une page à petit numéro
termine avant une page à numéro plus élevé déjà **en cours d'exécution** sur
une autre place.

**Créé paresseusement** (pas au niveau module) : les `Future` internes créées
par `PrioritySemaphore.acquire()` doivent être liées à la boucle d'événements
active au moment de leur création — le créer au niveau module risquerait de
le lier à la mauvaise boucle si le module est importé avant qu'`uvicorn` ne
démarre la sienne.

## Le prompt système (`SYSTEM_PROMPT`)

C'est le cœur de la qualité de la transcription. Points clés à connaître avant
de le modifier :

- **100% administratif** depuis le 2026-08-24 (aucune trace de mathématiques
  ou de LaTeX dans le texte du prompt — vérifié dans ce fichier).
- **Ratures** : instruction explicite d'ignorer ratures/gribouillages/notes
  illisibles, de ne transcrire que le contenu final propre.
- **Contenu incertain** (`==...==`) : encadre tout ce dont le modèle doute,
  même un seul caractère — jamais utilisé pour une rature (qui n'est pas
  transcrite du tout).
- **Convention tableaux — un bloc par ligne** : Claude doit émettre un bloc
  par ligne de données (pas un bloc pour tout le tableau), chaque bloc-ligne
  répétant l'en-tête + le séparateur (`|---|---|`) suivi de sa seule ligne de
  données. **Ne pas casser cette convention** : tout le frontend
  (`tableMarkdown.ts`, `TableRow.tsx`) et le post-traitement backend
  (`inject_confidence_column`, `_is_blank_table_row_block`) en dépendent.
  Claude ne doit **pas** ajouter lui-même de colonne "Confiance" — c'est le
  backend qui l'injecte automatiquement à partir du champ `confidence` du
  bloc (source de vérité unique).
- **Colonne "Annotation"** : si une cellule porte une écriture manuscrite dans
  une couleur nettement différente de l'original (souvent rouge sur noir/bleu),
  Claude doit créer une colonne "Annotation" dédiée à ce tableau plutôt que de
  mélanger les deux textes dans la même cellule.
- **Bbox en pixels absolus entiers**, jamais de fraction estimée par le
  modèle — les dimensions exactes de l'image envoyée sont rappelées dans
  `build_user_prompt()`.

Le prompt inclut un exemple JSON complet en français ("Fiche de présence",
élèves/notes) qui sert de référence de format — si vous le modifiez, gardez un
exemple cohérent avec les règles textuelles au-dessus.

## Mise en cache du prompt (`cache_control`)

```python
system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]
```

`SYSTEM_PROMPT` est identique à chaque appel (rien de variable par page/document
n'y figure). Avec `cache_control`, tant que le cache reste chaud côté Anthropic
(TTL 5 minutes, rafraîchi à chaque lecture — donc maintenu chaud pendant tout
le traitement d'un PDF, où les appels s'enchaînent), les appels suivants paient
**~90% moins cher** cette portion du prompt et bénéficient d'un temps de
réponse réduit. Purement additif — aucun changement de comportement.

## Retry et backoff (`_create_message_with_retry`, `_is_retryable_anthropic_error`)

**Pourquoi pas le retry intégré du SDK ?** Pour garder le contrôle du
backoff/logging/classification des erreurs, et pour pouvoir tenir la **même**
place de `PrioritySemaphore` pendant toute la séquence de tentatives d'une
page — impossible avec le retry interne du SDK, qui ferait tout ça à
l'intérieur d'un seul `await client.messages.create(...)`.

**Place de sémaphore tenue pendant tout le backoff (changé le 2026-09-21)** :
`_create_message_with_retry` acquiert **une seule fois** une place (`async
with _acquire_priority_slot(priority):` enveloppe toute la boucle de
tentatives), et la garde — y compris pendant l'`asyncio.sleep()` du backoff —
jusqu'à ce que la page ait fini d'essayer (succès ou tentatives épuisées).
**Comportement inversé par rapport à avant** : la place était auparavant
relâchée pendant le backoff, justement pour ne pas la monopoliser ; mais ça
permettait à une page neuve, jamais encore tentée, de doubler dans la file
d'attente du sémaphore une page en train de retenter — avec l'introduction de
`PrioritySemaphore`, ce doublage aurait sapé la priorité par numéro de page.
Contrepartie assumée : une place reste inoccupée (aucun appel réseau en
cours) pendant chaque backoff, réduisant le débit utile — jugé acceptable
puisque les retries restent l'exception.

- **Erreurs retentées** (`_is_retryable_anthropic_error`) : 429 (rate limit),
  toute erreur 5xx, ou un problème de connexion/timeout (pas de réponse HTTP
  du tout). Tout le reste (400, 401, 403, 404, 422...) est une erreur de fond
  qu'une nouvelle tentative ne résoudrait pas.
- **Nombre de tentatives** : `ANTHROPIC_MAX_RETRIES` (défaut 3) tentatives
  **supplémentaires** après la première.
- **Backoff exponentiel + jitter** : `ANTHROPIC_RETRY_BASE_DELAY_SECONDS`
  (défaut 1s) doublé à chaque tentative (1, 2, 4, 8...), plus un aléa de 0 à
  30% du délai — évite que plusieurs pages tombées en 429 en même temps (même
  compte, même rate limit) ne retentent toutes exactement au même instant.
- Une erreur non-retryable, ou la dernière tentative épuisée, est relevée
  **telle quelle** (même type d'exception) — les appelants (`call_anthropic_ocr`,
  puis les routers) la traitent exactement comme avant.

## Le pipeline complet (`call_anthropic_ocr`)

1. Valide que `ANTHROPIC_API_KEY` est configurée (`settings.validate()`).
2. Appelle `_create_message_with_retry()` avec le modèle (`ANTHROPIC_MODEL`,
   défaut `claude-sonnet-5`), `MAX_TOKENS` (défaut 8192), le prompt système
   caché, et l'image + le prompt utilisateur.
3. Si `message.stop_reason == "max_tokens"` : la réponse est traitée comme une
   **erreur de troncature** (`ValueError`), pas force-parsée comme JSON — un
   document avec un gros tableau peut dépasser `MAX_TOKENS` et être coupé en
   plein milieu du JSON. Solution : augmenter `MAX_TOKENS`.
4. Concatène le texte de la réponse, le passe à `parse_claude_response()`.

## Parsing de la réponse (`parse_claude_response`)

1. `extract_json_from_markdown()` retire les éventuelles balises ` ```json `.
2. `json.loads()` — une erreur ici devient `ValueError("La réponse de Claude n'est pas un JSON valide.")`.
3. `_is_blank_table_row_block()` filtre les blocs-ligne de tableau que Claude
   aurait générés sans donnée réelle (en-tête + séparateur présents mais
   3ᵉ ligne vide une fois les `|` retirés).
4. Chaque bbox est convertie de pixels absolus vers une fraction `[0,1]`, en
   divisant par `image_width`/`image_height` **exactement celles envoyées à
   Claude** (voir [`../architecture/02-flux-image.md`](../architecture/02-flux-image.md)
   pour pourquoi c'est important).
5. `inject_confidence_column()` ajoute la colonne "Confiance" à chaque
   bloc-tableau, à partir du champ `confidence` du bloc — jamais retranscrit
   par Claude, pour éviter toute incohérence avec la couleur affichée sur la
   bbox côté frontend.
6. Validation finale via `OCRResult.model_validate()` (Pydantic) — une
   erreur de schéma devient `ValueError`.

## Messages d'erreur utilisateur (`describe_anthropic_error`)

Classe chaque type d'erreur Anthropic en message français actionnable plutôt
que de renvoyer le JSON brut de l'API :

| Cas détecté | Message |
|---|---|
| 400 + "credit balance" dans le message | Crédits insuffisants, contacter l'administrateur |
| `AuthenticationError` (401) | Clé API invalide/expirée |
| `PermissionDeniedError` (403) | Accès refusé |
| `RateLimitError` (429) | Service surchargé, réessayer |
| `InternalServerError`/`OverloadedError` (5xx) | Indisponibilité temporaire côté Anthropic |
| `APIConnectionError` | Problème réseau/timeout |
| `RequestTooLargeError` (413) | Document trop volumineux |
| `BadRequestError` (400, autre) | Requête invalide + `str(exc)` |
| Tout le reste (404, 409, 422...) | `str(exc)` tel quel — c'est déjà un message Anthropic lisible, pas une trace interne |

Cette fonction est le **seul endroit** qui traduit une exception Anthropic en
message affiché côté frontend — étendre la classification ici suffit à
couvrir aussi bien le flux image que le flux PDF (page par page).

## Voir aussi

- [`../architecture/02-flux-image.md`](../architecture/02-flux-image.md) et [`03-flux-pdf.md`](../architecture/03-flux-pdf.md) — où ce service intervient dans le flux complet.
- [`07-configuration.md`](07-configuration.md) — toutes les variables `ANTHROPIC_*`.
- [`../operations-runbook.md`](../operations-runbook.md) — diagnostiquer une erreur Anthropic en production.
