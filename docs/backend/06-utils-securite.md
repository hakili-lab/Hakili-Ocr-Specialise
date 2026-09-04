# Utilitaires et sécurité — `image_utils.py` / `errors.py` / `security.py`

> Dernière vérification : commit `4a30eae`.

## `image_utils.py`

### Validation et lecture

- **`validate_content_type(content_type)`** : n'accepte que `image/png`,
  `image/jpeg`, `image/jpg` ; normalise `jpg` → `jpeg`. Lève `ValueError`
  sinon.
- **`read_upload_with_byte_limit(file, max_bytes, label)`** : lit `file` par
  blocs de 1 Mo, lève `ValueError` **dès que** la taille cumulée dépasse
  `max_bytes` — sans jamais bufferiser le reste du corps. C'est la fonction
  cœur, réutilisée pour deux usages différents :
  - **`read_upload_with_limit(file, max_mb, label)`** : budget en Mo, un seul
    appel HTTP (image, PDF envoyé d'un coup, corrections).
  - Directement, avec un **budget restant** calculé par l'appelant : lecture
    d'un morceau d'upload PDF fractionné (`upload_pdf_chunk`, où `max_bytes`
    est le budget qu'il reste au job, pas une constante fixe) — voir
    [`../architecture/04-flux-pdf-chunke.md`](../architecture/04-flux-pdf-chunke.md).
- **Pourquoi lire par blocs plutôt que `await file.read()` d'un coup** : un
  client qui envoie un fichier arbitrairement gros ne peut pas faire exploser
  la mémoire du serveur avant que la limite soit contrôlée.

### Traitement d'image

- **`normalize_orientation(raw_bytes, media_type)`** : réencode l'image avec
  l'orientation EXIF appliquée physiquement aux pixels
  (`ImageOps.exif_transpose`). Retourne les bytes bruts sans y toucher si
  l'image n'a pas d'orientation EXIF non standard, ou si elle est
  indécodable (log un warning, ne fait jamais échouer toute la requête pour
  un problème de métadonnées).
- **`resize_for_vision(raw_bytes, media_type)`** : redimensionne l'image aux
  dimensions calculées par `resized_size()` (voir ci-dessous), renvoie aussi
  la largeur/hauteur finales — réutilisées pour normaliser les bbox pixel
  renvoyées par Claude (voir
  [`../architecture/02-flux-image.md`](../architecture/02-flux-image.md)).
- **`resized_size(width, height, max_edge=1568, max_tokens=1568)`** :
  calcule les plus grandes dimensions qui respectent à la fois la limite de
  bord (arrondie au multiple de 28 supérieur — la taille de tuile du calcul
  de tokens Vision de Claude) et le budget de tokens
  (~`⌈largeur/28⌉ × ⌈hauteur/28⌉`). Recherche dichotomique sur la plus grande
  dimension satisfaisant les deux contraintes à ratio d'aspect constant — pas
  un simple ratio d'échelle, car l'arrondi au multiple de 28 rend la fonction
  "en escalier".
- **`encode_bytes_to_base64(raw_bytes)`** : trivial.

### PDF → images

- **`count_pdf_pages(raw_bytes, max_pages=None)`** : compte les pages
  **sans rendre le moindre pixel** — ouvre juste la structure du document.
  Nettement moins coûteux que `convert_pdf_to_images`, utilisé pour valider un
  budget de pages avant de décider de rasteriser (flux chunké).
- **`convert_pdf_to_images(raw_bytes, dpi=150, max_pages=None)`** : convertit
  chaque page en PNG à la résolution donnée via PyMuPDF. Lève `ValueError`
  **avant** de rendre la moindre page si `max_pages` est dépassé.
  **Rendu séquentiel, volontairement** : PyMuPDF ne libère pas le GIL Python
  pendant `page.get_pixmap()`, donc un `ThreadPoolExecutor` n'apporterait
  quasi rien (~10%, mesuré) — un vrai parallélisme demanderait un pool de
  *processus*, jugé disproportionné pour le gain (voir
  [`../decisions-et-limites-connues.md`](../decisions-et-limites-connues.md)
  pour le détail du benchmark et pourquoi ce n'est pas fait).
- **Import différé de `fitz`** (PyMuPDF) dans les deux fonctions ci-dessus —
  pour ne pas rendre le démarrage du serveur dépendant de sa présence.

## `errors.py` — assainissement des erreurs inattendues

```python
def log_unexpected(logger, context, public_message) -> str:
    error_id = uuid.uuid4().hex[:8]
    logger.exception("%s [ref=%s]", context, error_id)
    return f"{public_message} (référence : {error_id})"
```

**À appeler dans un bloc `except`** pour toute exception non prévue. Convention
née de la revue de sécurité du 2026-08-13 (item #4) : `str(exc)` d'une
exception non prévue peut contenir des chemins de fichiers, le schéma/chemin
de la base SQLite, des bouts de l'entrée fournie, ou des internes de
librairies (PyMuPDF, PIL, WeasyPrint) — autant d'informations qui offriraient
gratuitement une carte des internes du serveur à quiconque sonde l'API.

- La **trace complète** est loguée côté serveur (`logger.exception`, avec la
  stack trace).
- Le **client ne reçoit que** `public_message` suffixé d'une référence courte
  (8 caractères hex) — à citer dans un rapport de bug pour retrouver la trace
  exacte côté serveur (`grep ref=xxxxxxxx` dans les logs).

**Utilisez systématiquement cette fonction** dans tout nouveau `except Exception`
qui pourrait remonter jusqu'au client — ne renvoyez jamais `str(exc)` d'une
exception non explicitement prévue. Voir [`../security.md`](../security.md)
pour la politique complète.

## `security.py` — vérification de la clé API

```python
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

async def verify_api_key(provided_key: str | None = Security(_api_key_header)) -> None:
    settings = get_settings()
    if not settings.APP_API_KEY or provided_key != settings.APP_API_KEY:
        raise HTTPException(status_code=401, detail="Clé API invalide ou manquante.")
```

Dépendance FastAPI injectée sur tous les routers via
`dependencies=[Depends(verify_api_key)]` (transcription, corrections, export
— **pas** `/` ni `/health`). Deux comportements à connaître :

- **Si `APP_API_KEY` n'est pas configuré côté serveur, tout est refusé** —
  jamais d'acceptation silencieuse par défaut. Un backend mal configuré
  (variable oubliée) refuse toutes les requêtes protégées plutôt que de les
  laisser passer sans vérification.
- **Comparaison `!=`, pas `secrets.compare_digest`** — non constant-time,
  décision assumée (voir [`../security.md`](../security.md) pour le
  raisonnement).

Cette clé (`X-API-Key`) est distincte de `ANTHROPIC_API_KEY` : l'une
authentifie le frontend auprès de **ce** backend, l'autre authentifie ce
backend auprès de **l'API Anthropic**. Ne jamais les confondre en configurant
l'environnement.

## Voir aussi

- [`../security.md`](../security.md) — la politique de sécurité complète du projet, dont ces deux mécanismes ne sont qu'une partie.
- [`../architecture/02-flux-image.md`](../architecture/02-flux-image.md) — où `image_utils.py` intervient dans le pipeline.
- [`07-configuration.md`](07-configuration.md) — `APP_API_KEY`, `MAX_IMAGE_SIZE_MB`, `MAX_PDF_SIZE_MB`, `MAX_PDF_PAGES`.
