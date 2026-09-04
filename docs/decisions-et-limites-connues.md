# Décisions structurantes et limites connues

> Dernière vérification : commit `4a30eae`. Ce fichier existe pour éviter qu'un nouveau développeur ne "corrige" un choix qui a en réalité été délibérément fait — chaque entrée explique le **pourquoi**, pas seulement le quoi.

## Pivot produit : mathématiques → documents administratifs (2026-08-24)

Le produit a été repositionné depuis "transcription de copies de
mathématiques manuscrites en LaTeX" vers son usage actuel : documents
administratifs (tableaux, formulaires, relevés, listes). `SYSTEM_PROMPT` et
`build_user_prompt()` (`claude_service.py`) ont été **entièrement réécrits**
en conséquence — plus aucune mention de mathématiques dans le prompt actif.

**Le contrat JSON/bbox n'a pas changé** (`id`/`label`/`markdown`/`bbox`/
`confidence`, les marqueurs `==...==`, la convention un-bloc-par-ligne-de-tableau,
la colonne d'annotation couleur) : il était déjà indépendant du domaine, donc
rien à modifier côté schémas ou côté rendu frontend.

**Traces cosmétiques du passé mathématique, volontairement non nettoyées** :

| Endroit | Trace | Impact |
|---|---|---|
| `hakili-ocr/src/components/UploadScreen.tsx` | Étiquette "FORMULES" sur l'écran d'upload | Visuel uniquement |
| `hakili-ocr/src/components/LoadingScreen.tsx` | Texte de statut "Vérification des formules…" | Visuel uniquement |
| `ocr-math-api/app/main.py` | Titre Swagger "OCR Math API", description mentionnant "mathématiques manuscrites en LaTeX" | Visible sur `/docs` uniquement |
| `README.md` (racine) | Description du projet encore formulée autour des mathématiques | Documentation uniquement |
| Rendu KaTeX (frontend) | `remark-math`, `rehype-katex`, `markdownHighlight.ts` (`preprocessMathHighlights`) restent en place et fonctionnels | Simplement inutilisé désormais — le prompt actuel ne génère plus de LaTeX, mais rien n'empêche techniquement un bloc de contenir des formules si un document en avait |
| Noms de dossiers/projets | `ocr-math-api`, dossier `hakili-lab-ocr-app` | Cosmétique, coût de renommage jugé non prioritaire |

**Si vous nettoyez l'un de ces éléments**, faites-le par petites touches
ciblées — ce ne sont pas des bugs, juste un nettoyage cosmétique resté en
suspens lors du pivot.

## Store de jobs PDF en mémoire (`job_store.py`)

**Décision** : un simple `dict[str, PDFJob]` en mémoire du process Python,
pas de Redis ni de base partagée.

**Pourquoi** : l'app est pensée pour un déploiement **instance unique,
montée verticalement** — pas de scaling horizontal. Ajouter un store partagé
maintenant serait de la sur-ingénierie pour un besoin non encore avéré.

**Conséquences concrètes** :
- Tout job PDF en cours est **perdu** au redémarrage du backend.
- **Incompatible avec plusieurs workers/processus/replicas** — un job créé sur
  un worker serait invisible pour une requête de statut routée vers un autre.

**Revoir cette décision si** : le déploiement doit un jour passer à plusieurs
instances/workers (montée en charge horizontale). Détail :
[`backend/03-service-jobs-pdf.md`](backend/03-service-jobs-pdf.md).

## Corrections en SQLite + fichiers sur disque (`correction_store.py`)

**Décision** : SQLite (stdlib, sans ORM) + PNG sur disque, cohérent avec la
même contrainte d'instance unique que `job_store.py`.

**Limite connue** : sous forte concurrence d'écriture, `sqlite3` peut renvoyer
`database is locked`. **Le correctif prévu, à faible coût, est
`PRAGMA journal_mode=WAL`** — pas une migration vers un autre SGBD, les
corrections restant une action à faible fréquence, pas le chemin critique de
l'app. Voir [`backend/04-service-corrections-export.md`](backend/04-service-corrections-export.md).

## Rasterisation PDF séquentielle, pas de pool de processus (2026-08-28)

**Contexte** : l'idée de paralléliser `convert_pdf_to_images()`
(`image_utils.py`) via un pool de threads/processus a été **étudiée puis
écartée**, benchmarkée sur une machine 16 vCPU (16 pages, 150 DPI) :

| Approche | Temps | Gain |
|---|---|---|
| Séquentiel | 12.0s | référence |
| `ThreadPoolExecutor` ×4 | 10.8s | ~10% |
| `ProcessPoolExecutor` ×4 | 3.9s | ~3× |

**Pourquoi le gain thread est si faible** : PyMuPDF **ne libère pas le GIL**
Python pendant `page.get_pixmap()` — seul l'encodage PNG (`pix.tobytes("png")`)
le libère, ce qui explique le maigre gain du thread pool.

**Pourquoi le pool de processus (le seul vrai gain) a été écarté** : il
faudrait sérialiser (pickler) le PDF entier vers chaque worker (jusqu'à
`MAX_PDF_SIZE_MB`, potentiellement 500 Mo → nécessiterait un handoff par
fichier temporaire), gérer le cycle de vie du pool par worker Uvicorn, et des
processus de rendu qui pègrent le CPU pourraient affamer le traitement des
requêtes normales. Jugé disproportionné pour le gain, d'autant que la
rasterisation tourne déjà hors de la boucle d'événements
(`asyncio.to_thread`) — elle n'ajoute du temps qu'au job concerné, déjà
adouci par l'affichage progressif des pages.

**Ne rouvrez pas ce sujet sans** soit un déploiement multi-instances qui
rendrait la latence de rasterisation critique, soit une version de PyMuPDF
qui documenterait une libération du GIL pendant le rendu. Détail :
[`backend/06-utils-securite.md`](backend/06-utils-securite.md).

## Pas de limitation de débit (rate limiting) API

**Décision explicite de l'utilisateur** (2026-08-28), voir
[`security.md`](security.md) pour le détail complet — risque accepté pour un
déploiement privé à audience connue. Le backstop est une alerte de dépense
configurée sur le compte Anthropic, **en dehors du code**.

## Comparaison de clé API non constant-time

**Décision explicite de l'utilisateur** (2026-08-28), voir
[`security.md`](security.md) — jugée non risquée dans ce contexte (clé déjà
non secrète une fois le frontend déployé, signal de timing négligeable face à
la latence réseau).

## Variables `VITE_*` figées au build, pas au runtime

**Décision** : `VITE_API_BASE_URL`/`VITE_APP_API_KEY` sont passées comme
build args Docker, pas comme variables d'environnement du conteneur.

**Conséquence assumée** : une seule image frontend construite = liée à un
seul backend cible. Changer de backend (autre environnement) nécessite un
**rebuild**, pas juste un redémarrage. **À revisiter si** plusieurs
environnements doivent un jour partager une seule image (solution possible :
un entrypoint qui substitue un placeholder dans le JS/HTML déjà buildé, pas
implémenté aujourd'hui). Détail :
[`frontend/07-configuration.md`](frontend/07-configuration.md) et
[`deployment/01-docker-compose.md`](deployment/01-docker-compose.md).

## CSP jamais vérifiée en conteneur réel

Ajoutée le 2026-08-28, validée seulement statiquement (rendu `envsubst`,
`docker compose config`, `npm run build`) au moment de la dernière revue —
**pas encore observée en conditions réelles** (`docker compose up` + usage
normal de l'app en surveillant la console navigateur pour des violations).
C'est le seul point encore "ouvert" de la revue de sécurité de 2026-08.
Détail : [`deployment/02-nginx-csp.md`](deployment/02-nginx-csp.md).

## Limite connue de l'affichage progressif PDF

Si la **page 1** échoue définitivement mais que les pages suivantes
réussissent, rien ne s'affiche côté frontend tant que le job entier n'est pas
terminé — `takeReadyPagePrefix()` ne révèle jamais rien sans la page 1
présente. Accepté tel quel (les échecs de page individuelle sont rares en
pratique). Détail : [`../architecture/03-flux-pdf.md`](architecture/03-flux-pdf.md).

## Inefficacités connues, non corrigées

- **`PreviewScreen.tsx`/`usePdfPreview`** (rendu de *chaque* page en PNG
  complet, aucun Web Worker de rendu — seul le décodage pdfjs est en Worker)
  et **`fileTransform.ts`/`rotatePdfFile`** (`PDFDocument.load` + `save()`
  complet, uniquement si une rotation a réellement eu lieu) parsent/rendent
  chacun indépendamment le même document PDF, avant que `loadPdf()`
  (`pdfChunking.ts`) ne le fasse une troisième fois pour le comptage de pages.
  Pour un document de plusieurs centaines de pages, le contenu du fichier est
  donc parcouru plusieurs fois côté client. **Accepté tel quel** — un rendu
  d'aperçu paresseux (page par page, à la demande de navigation) serait le
  correctif si cela devient un vrai problème perçu par les utilisateurs.

## Voir aussi

- [`security.md`](security.md) — les décisions de sécurité en détail, avec leur justification complète.
- [`operations-runbook.md`](operations-runbook.md) — comment ces limites se manifestent concrètement en production.
- [`architecture/01-vue-ensemble.md`](architecture/01-vue-ensemble.md) — le contexte produit derrière le pivot mathématiques → administratif.
