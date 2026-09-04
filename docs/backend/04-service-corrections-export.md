# Corrections et export PDF — `correction_store.py` / `pdf_export_service.py`

> Dernière vérification : commit `4a30eae`.

## Corrections utilisateur (`correction_store.py`)

### Rôle

Quand un utilisateur corrige le texte d'un bloc dans l'interface, l'app peut
capturer cette correction (image croppée sur la zone concernée + texte
original + texte corrigé + description de l'erreur) pour constituer, à terme,
un jeu de données exploitable pour améliorer le prompt ou le modèle. C'est de
la **collecte de données, pas une fonctionnalité utilisateur visible** au-delà
de la petite modale qui demande "quelle était l'erreur ?" après chaque
correction.

### Stockage

- **SQLite** (stdlib `sqlite3`, aucune dépendance ORM) — une seule table
  `corrections` (id, image_path, block_label, confidence, original_markdown,
  corrected_markdown, error_description, created_at).
- **Images PNG sur disque**, dans `ocr-math-api/data/images/`, nommées par
  l'id de la correction (uuid4 hex) — ce même id sert de clé primaire en base,
  ce qui évite toute jointure ou lookup pour retrouver l'image d'une ligne.
- `DATA_DIR` est ancré sur `__file__` (`Path(__file__).resolve().parent.parent.parent / "data"`),
  **pas** sur le répertoire de travail courant — le chemin ne dépend donc pas
  de l'endroit d'où `uvicorn` est lancé.
- En Docker, `data/` est un **volume nommé** (`backend_data`), jamais copié
  dans l'image — persiste entre les redéploiements.

### Pourquoi une connexion SQLite par appel (`_execute`)

```python
def _execute(query, params=()):
    conn = sqlite3.connect(DB_PATH)
    try:
        with conn:
            conn.execute(query, params)
    finally:
        conn.close()
```

`sqlite3.Connection` **n'est pas thread-safe**. `save_correction()` est
invoquée via `asyncio.to_thread` côté router (`corrections.py`), donc
potentiellement depuis un thread différent à chaque appel — une connexion
partagée façon `_get_client()` (le client Anthropic) serait incorrecte ici.
Chaque appel ouvre, exécute dans sa propre transaction, referme.

**Si vous voyez des erreurs `database is locked` en production** : c'est un
problème de concurrence d'écriture SQLite, attendu si le volume de corrections
devient significatif. Le correctif à faible coût est `PRAGMA journal_mode=WAL`
— **pas** une migration vers un autre SGBD (les corrections restent une
action à faible fréquence, pas le chemin critique de l'app).

## Export PDF (`pdf_export_service.py`)

### Rôle et approche

Génère un **vrai PDF vectoriel** (texte sélectionnable) de la transcription,
plutôt qu'une capture d'écran rasterisée. C'est un choix délibéré de seconde
génération : une première approche (`html2canvas` + `jsPDF`, côté frontend
uniquement) produisait un PDF sans texte sélectionnable.

### Comment ça marche

1. Le frontend rend la transcription **hors-écran** avec React + KaTeX
   (`TranscriptExport.tsx`, voir
   [`../frontend/04-composants-result.md`](../frontend/04-composants-result.md)),
   attend deux `requestAnimationFrame` pour que KaTeX ait fini de peindre les
   formules, puis sérialise le `.innerHTML` obtenu.
2. Ce fragment HTML est envoyé tel quel dans `POST /export/pdf` (`{ html }`).
3. `render_export_pdf()` enveloppe ce fragment dans un document HTML complet
   avec un template d'export dédié (`_EXPORT_TEMPLATE`) : feuille de style
   `@page` (format A4, marges), tout le texte forcé en noir, tableaux qui ne se
   coupent jamais entre deux pages (`break-inside: avoid`), cellules qui
   passent à la ligne plutôt que d'être tronquées.
4. **WeasyPrint** (`HTML(string=...).write_pdf()`) convertit ce HTML/CSS en
   PDF **sans exécuter le moindre JavaScript** — puisque KaTeX a déjà produit
   du HTML/CSS statique côté frontend, WeasyPrint n'a besoin que de la feuille
   de style KaTeX (copiée localement dans `app/static/katex/katex.min.css`)
   pour positionner les glyphes correctement.

### Import différé de `weasyprint`

```python
def render_export_pdf(content_html: str) -> bytes:
    from weasyprint import HTML   # import DANS la fonction, pas en haut du fichier
    ...
```

Sur Windows, l'import de `weasyprint` échoue tant que le runtime natif GTK3
(Pango/GObject) n'est pas installé séparément sur la machine. Un import au
niveau module ferait **planter le démarrage de toute l'API** — y compris les
endpoints qui n'ont rien à voir avec l'export — dès que ce runtime est absent.
L'import différé limite l'échec au seul appel de `POST /export/pdf`.

L'image Docker du backend installe les bibliothèques nécessaires
(`libpango-1.0-0`, `libpangoft2-1.0-0`, `fonts-dejavu-core`) — l'export
fonctionne donc "out of the box" en conteneur, quelle que soit la machine hôte.

**Symptôme si le runtime est absent en dehors de Docker** : `POST /export/pdf`
renvoie 500 avec une erreur liée à `weasyprint`/`cairo`/`pango` dans les logs
serveur (jamais exposée telle quelle au client — voir
[`../security.md`](../security.md)) ; tous les autres endpoints continuent de
fonctionner normalement.

## Voir aussi

- [`01-routers.md`](01-routers.md) — `corrections.py` et `export.py`, les endpoints qui appellent ces deux services.
- [`../frontend/03-hooks.md`](../frontend/03-hooks.md) — `useCorrectionCapture`, côté frontend.
- [`../frontend/04-composants-result.md`](../frontend/04-composants-result.md) — `TranscriptExport.tsx`, le template hors-écran sérialisé.
- [`../deployment/01-docker-compose.md`](../deployment/01-docker-compose.md) — le volume `backend_data`.
