# Suivi des jobs PDF — `job_store.py`

> Dernière vérification : commit `4a30eae`. Code : `ocr-math-api/app/services/job_store.py`.

## Rôle

Permet au frontend de suivre la progression d'un traitement PDF (potentiellement
long) par polling, au lieu d'attendre une seule requête HTTP bloquante jusqu'à
la fin. Utilisé aussi bien par le flux PDF classique que par le flux chunké
(voir [`../architecture/03-flux-pdf.md`](../architecture/03-flux-pdf.md) et
[`04-flux-pdf-chunke.md`](../architecture/04-flux-pdf-chunke.md)).

## ⚠️ Limite structurelle la plus importante à connaître

```python
_jobs: dict[str, PDFJob] = {}
```

Le store est un **simple dictionnaire en mémoire du process Python**. Cela
signifie :

- **Tous les jobs sont perdus au redémarrage** du backend (déploiement,
  crash, redémarrage du conteneur).
- **Incompatible avec plusieurs workers/processus** — si le backend tournait
  un jour derrière plusieurs workers Uvicorn/Gunicorn ou plusieurs instances,
  un job créé sur le worker A serait invisible pour une requête de statut
  routée vers le worker B.

C'est un choix assumé, cohérent avec la contrainte de déploiement
**"instance unique, montée verticalement"** du projet (voir
[`../deployment/01-docker-compose.md`](../deployment/01-docker-compose.md) et
[`../decisions-et-limites-connues.md`](../decisions-et-limites-connues.md)).
**Ne changez pas le nombre de workers/replicas sans d'abord migrer ce store
vers quelque chose de partagé (Redis, etc.)** — sinon les statuts de job
deviennent aléatoirement incohérents selon le worker qui répond.

## Structure de `PDFJob`

```python
@dataclass
class PDFJob:
    job_id: str
    pages_total: int
    pages_done: int = 0
    status: JobStatus = "processing"       # "processing" | "done" | "error"
    result: Optional[PDFTranscriptionResult] = None
    error: Optional[str] = None
    created_at: float = ...

    # --- Champs utilisés UNIQUEMENT par un job "chunké" ---
    pages_expected: Optional[int] = None   # None = job "legacy" (discriminant)
    pages_received: int = 0
    bytes_received: int = 0
    chunks_pending: int = 0
    upload_finalized: bool = False
    updated_at: float = ...
    results: dict[int, PageResult] = {}
    warnings: list[tuple[int, str]] = []
    errors: list[tuple[int, str]] = []
    lock: asyncio.Lock = ...
```

Un job créé par le flux **classique** (`create_job`) laisse tous les champs
"chunkés" à leurs valeurs par défaut et ne les touche jamais.
**`pages_expected is None` est le discriminant** utilisé partout dans le code
pour distinguer un job legacy d'un job chunké — pas de booléen séparé.

Pourquoi `results`/`warnings`/`errors` sont des **champs du job** (et pas des
variables locales à une fonction) : un job chunké est alimenté par plusieurs
vagues de tâches successives (une par morceau reçu), pas par un seul appel de
fonction — ces champs doivent survivre entre les requêtes HTTP successives qui
alimentent le même job.

`lock: asyncio.Lock` empêche deux morceaux d'être lus/comptés en même temps
pour un même job — tenu **uniquement** pendant la lecture + le comptage bon
marché des pages (`count_pdf_pages`), jamais pendant la rasterisation ni l'OCR
(déportés en tâche de fond). Construit directement via
`field(default_factory=asyncio.Lock)` — contrairement au sémaphore global
paresseux de `claude_service.py`, un `PDFJob` n'est jamais instancié en dehors
d'un handler de requête FastAPI, donc toujours déjà à l'intérieur d'une boucle
d'événements active. **Ne "corrigez" pas ça en le rendant paresseux** — ce
n'est pas nécessaire ici et ajouterait de la complexité pour rien.

## Fonctions exposées

| Fonction | Rôle |
|---|---|
| `create_job(pages_total)` | Crée un job "legacy" (fichier PDF déjà complet) |
| `create_chunked_job(pages_expected)` | Crée un job chunké ; plafonne `pages_expected` à `MAX_PDF_PAGES` **avant** de l'utiliser comme budget cumulé — un client ne peut pas contourner la limite en étalant les pages sur plusieurs petits morceaux |
| `get_job(job_id)` | Retourne le `PDFJob` ou `None` |
| `_purge_expired_jobs()` | Appelée à chaque création de job (pas de tâche planifiée séparée) — voir ci-dessous |

## Purge des jobs expirés

Deux catégories de jobs sont supprimées à chaque appel de `create_job`/`create_chunked_job` :

1. **Jobs terminés depuis longtemps** — `status in ("done", "error")` et plus
   vieux que `JOB_TTL_SECONDS` (défaut 4h). Évite une croissance indéfinie du
   dictionnaire en mémoire sur un process qui tourne longtemps.
2. **Jobs chunkés bloqués** — `status == "processing"`, `upload_finalized == False`,
   et pas de nouveau morceau reçu depuis plus de `JOB_STALL_TIMEOUT_SECONDS`
   (défaut 30 min). Sans cette règle, un client qui abandonne un upload par
   morceaux en cours de route (onglet fermé, connexion coupée) laisserait un
   job `"processing"` indéfiniment impurgeable — rien d'autre ne le ferait
   jamais passer à `done`/`error`.

**Implication pratique pour le diagnostic** : si un job PDF semble "disparu"
côté frontend (404 sur `/pdf/status/{job_id}`) alors qu'il devrait encore
exister, vérifiez d'abord s'il a dépassé l'un de ces deux délais avant de
chercher un bug ailleurs. Voir [`../operations-runbook.md`](../operations-runbook.md).

## Voir aussi

- [`01-routers.md`](01-routers.md) — les fonctions du router qui créent/lisent/modifient un `PDFJob`.
- [`../architecture/03-flux-pdf.md`](../architecture/03-flux-pdf.md) et [`04-flux-pdf-chunke.md`](../architecture/04-flux-pdf-chunke.md) — les flux complets qui utilisent ce store.
- [`07-configuration.md`](07-configuration.md) — `JOB_TTL_SECONDS`, `JOB_STALL_TIMEOUT_SECONDS`, `MAX_PDF_PAGES`.
