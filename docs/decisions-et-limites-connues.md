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

## OOM en production + retuning des paramètres de traitement PDF (2026-09-21)

**Incident** : sur le serveur de déploiement (VPS `guichet-entrepreneur`,
3.7 Gi de RAM, **sans swap**), le backend a été tué par le noyau (`exited
with code 137`, SIGKILL) en pleine session de traitement PDF chunké, juste
après le retour de deux appels Anthropic. `docker-compose.yml` relance le
service (`restart: unless-stopped`), mais `job_store.py` étant en mémoire
process (voir "Known limitation" plus haut / `01-vue-ensemble.md`), le job en
cours a été perdu — le frontend a continué à poller un `job_id` qui n'existait
plus dans le nouveau process (`404` en boucle). `free -h` a confirmé une marge
mémoire déjà très étroite au repos (254 Mi libres sur 3.7 Gi), cause quasi
certaine malgré l'absence d'accès `dmesg` pour une confirmation noyau directe.

**Changements appliqués** (tous par défaut, `.env`/constantes ; à répercuter
sur le `.env` du serveur qui ne suit pas ce dépôt) :

| Paramètre | Avant | Après | Raison |
|---|---|---|---|
| `ANTHROPIC_CONCURRENCY` | 6 | 2 | Réduit le pic mémoire du traitement PDF parallèle (moins d'images rasterisées + réponses Claude en mémoire simultanément). D'abord baissé à 3, puis à **2** (voir ci-dessous) faute de pouvoir ajouter un filet de sécurité swap. Contrepartie acceptée : débit plus faible sur un document long. |
| `PDF_POLL_INTERVAL_MS` | 8000 | 4000 | Réduit la latence d'affichage perçue (délai entre "page prête côté backend" et "page visible à l'écran") sans multiplier la charge par 4 (2s envisagé puis écarté). |
| `PDF_CHUNK_SIZE_PAGES` | 20 | 10 | Toujours largement au-dessus d'`ANTHROPIC_CONCURRENCY` (2) pour saturer le sémaphore ; un morceau plus petit se rasterise plus vite, donc la première page d'un morceau apparaît plus tôt. |
| `PDF_CHUNK_PAGE_COUNT_THRESHOLD` | 30 | 10 | Le flux `/pdf/start` (non chunké) rasterise *toutes* les pages avant de créer le job — pour un document de taille moyenne, ça retardait le tout premier appel Claude (donc la première page affichée). Abaisser le seuil fait basculer plus tôt vers le flux chunké, qui rasterise en arrière-plan par morceau. |
| `MAX_TOKENS` | 24576 | 12288 | Le crash a persisté même à `ANTHROPIC_CONCURRENCY=2`, confirmé (`docker compose exec backend env`) réellement pris en compte — signe que la marge mémoire de base de la machine (3.2 Gi déjà utilisés sur 3.7 Gi au repos) est le facteur limitant, pas seulement le parallélisme. `MAX_TOKENS` avait été triplé (8192 → 24576) sans lien avec ce diagnostic OOM ; une réponse Claude plus longue autorisée est bufferisée intégralement en mémoire avant parsing — redescendu à mi-chemin (12288) pour réduire ce facteur sans réintroduire le risque de troncature qui avait motivé la hausse initiale. |

**Swap non ajouté — pas d'accès root sur le serveur de déploiement** :
le compte applicatif (`hakili-ocr`) n'a pas de droits sudo (`sudo fallocate…`
refusé), et il n'existe pas d'autre compte root/admin disponible au moment de
cette entrée. Ajouter du swap nécessite soit une intervention de
l'hébergeur, soit un accès root regagné — non fait tant que ça n'est pas
possible. En compensation, `ANTHROPIC_CONCURRENCY` a été baissé plus bas que
prévu initialement (3 → **2**) : sans filet de sécurité mémoire côté OS, le
seul levier restant pour limiter le risque d'OOM est de réduire encore le
pic côté application. **À revisiter** : remonter à 3 (voire plus) dès que le
swap est en place.

**Non corrigé par ce retuning** (causes structurelles distinctes, laissées
en l'état) :
- `LoadingScreen.tsx` n'affiche aucune progression réelle avant que la
  première page ne soit prête (simplification volontaire du 2026-08-24,
  spinner générique + texte en rotation) — régression perçue par rapport à
  l'ancien compteur "Page X / Y", mais pas de changement fonctionnel ici.
- Le flux `/pdf/start` (documents ≤ 10 pages désormais) rasterise toujours
  toutes ses pages de façon synchrone avant de créer le job — non corrigé,
  changement d'architecture plus lourd.

## Test local du sémaphore à priorité + place tenue pendant le retry (2026-09-21)

**Contexte** : diagnostic de la limite déjà notée plus haut ("Limite connue de
l'affichage progressif PDF") aggravée par le comportement du sémaphore de
concurrence : les pages d'un job PDF sont lancées en parallèle
(`asyncio.gather`) et servies dans l'ordre FIFO d'arrivée à la file du
sémaphore — une page qui tombe en erreur transitoire (429, 5xx) et retente
relâchait sa place pendant le backoff (design d'origine, voir
`02-service-claude.md`), ce qui laissait des pages jamais encore tentées la
doubler dans la file. Combiné au fait que le frontend (`takeReadyPagePrefix`)
n'affiche que le préfixe contigu de pages prêtes à partir de la page 1, ça
pouvait provoquer un long silence suivi d'un "burst" de plusieurs pages d'un
coup dès que la page 1 finissait enfin (observé concrètement sur un test
local avec un PDF de 75 pages : longue attente pour la page 1, puis ~6 pages
révélées d'un coup).

**Changement de code** : `claude_service.py` gagne une classe
`PrioritySemaphore` (tas min `heapq`, priorité = numéro de page) à la place
d'`asyncio.Semaphore`, et `_create_message_with_retry` acquiert désormais sa
place **une seule fois pour toute la séquence de tentatives** d'une page
(gardée pendant le backoff), au lieu de la relâcher entre deux tentatives.
Voir le docstring de `PrioritySemaphore` et de `_create_message_with_retry`
pour le détail et les limites assumées : ça ne garantit pas l'ordre de
complétion entre pages déjà en vol (seules les tâches en attente sont
réordonnées), et ça réduit le débit utile pendant un backoff (une place reste
inoccupée). Vérifié par un script manuel isolé,
`ocr-math-api/tests/test_priority_semaphore.py` (sémantique de comptage,
ordre par priorité, tie-break FIFO, deux cas d'annulation) — les cinq
vérifications passent.

**Paramètres relevés temporairement, TEST LOCAL UNIQUEMENT** (pas pour la
production tant que le swap n'est pas en place — voir la section OOM
ci-dessus) :

| Paramètre | Valeur production (inchangée) | Valeur de test local |
|---|---|---|
| `ANTHROPIC_CONCURRENCY` | 2 | 6 |
| `PDF_CHUNK_SIZE_PAGES` | 10 | 12 |

Ces deux valeurs ont été relevées ensemble dans les fichiers `.env`/`.env.example`
du dépôt (racine et `ocr-math-api/`) et dans les valeurs par défaut de
`config.py`/`pdfChunking.ts`, uniquement pour avoir assez de pages en vol
simultanément lors d'un test local et observer un effet du nouvel
ordonnancement par priorité. **Le `.env` du serveur de production ne suit pas
ce dépôt** (voir la section OOM ci-dessus) : il garde
`ANTHROPIC_CONCURRENCY=2` indépendamment de ce que dit `config.py`. Si ce
dépôt est un jour redéployé depuis un checkout neuf sans `.env` de production
déjà en place, il faut explicitement redéfinir `ANTHROPIC_CONCURRENCY=2` (et
la taille de morceau associée côté frontend) avant tout déploiement réel — ne
pas se fier au défaut de code tant que le swap n'est pas en place.

## Voir aussi

- [`security.md`](security.md) — les décisions de sécurité en détail, avec leur justification complète.
- [`operations-runbook.md`](operations-runbook.md) — comment ces limites se manifestent concrètement en production.
- [`architecture/01-vue-ensemble.md`](architecture/01-vue-ensemble.md) — le contexte produit derrière le pivot mathématiques → administratif.
