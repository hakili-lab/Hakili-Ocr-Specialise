# Docker et docker-compose

> Dernière vérification : commit `4a30eae`. Code : `docker-compose.yml`, `ocr-math-api/Dockerfile`, `hakili-ocr/Dockerfile`, `.env.example` (racine).

## Vue d'ensemble

```
docker-compose.yml (racine)
├── service "backend"   — build depuis ./ocr-math-api, host :8020 → container :8000
└── service "frontend"  — build depuis ./hakili-ocr,  host :8021 → container :80
```

```bash
cp .env.example .env             # racine — renseigner ANTHROPIC_API_KEY, APP_API_KEY, etc.
docker compose build
docker compose up -d
docker compose logs -f backend   # ou: docker compose logs -f (les deux services)
docker compose down              # arrête ; le volume backend_data persiste
```

Après une modification de code :

```bash
docker compose build --no-cache   # --no-cache si un layer semble "coincé"
docker compose up -d
```

## Service `backend`

```yaml
backend:
  build:
    context: ./ocr-math-api
  env_file:
    - .env
  ports:
    - "8020:8000"
  volumes:
    - backend_data:/app/data
  restart: unless-stopped
```

- **`env_file: .env`** : toutes les variables du `.env` racine (voir
  [`../backend/07-configuration.md`](../backend/07-configuration.md)) sont
  injectées comme variables d'environnement du conteneur au **runtime** — un
  changement de `.env` + `docker compose up -d` (sans rebuild) suffit à les
  répercuter, puisque `config.py` les lit à chaque démarrage du process
  Python.
- **`volumes: backend_data:/app/data`** : le dossier `data/` (SQLite +
  images de corrections, voir
  [`../backend/04-service-corrections-export.md`](../backend/04-service-corrections-export.md))
  est un **volume Docker nommé**, jamais copié dans l'image — il **persiste**
  entre les redéploiements (`docker compose down` puis `up` ne le supprime
  pas ; il faudrait un `docker compose down -v` explicite pour ça, à ne
  **jamais** faire sans avoir sauvegardé le contenu si les corrections ont de
  la valeur).

## Service `frontend`

```yaml
frontend:
  build:
    context: ./hakili-ocr
    args:
      VITE_API_BASE_URL: ${VITE_API_BASE_URL}
      VITE_APP_API_KEY: ${APP_API_KEY}
  environment:
    API_ORIGIN: ${VITE_API_BASE_URL}
    NGINX_ENVSUBST_FILTER: API_ORIGIN
  ports:
    - "8021:80"
  depends_on:
    - backend
  restart: unless-stopped
```

**Deux mécanismes de configuration bien distincts à ne pas confondre** :

1. **`args` (build args)** : `VITE_API_BASE_URL`/`VITE_APP_API_KEY` sont
   passées à `docker build` et figées **dans le bundle JavaScript** au moment
   du `npm run build` (voir
   [`../frontend/07-configuration.md`](../frontend/07-configuration.md)) —
   **un rebuild de l'image est nécessaire** pour changer de backend cible, un
   simple restart ne suffit pas.
2. **`environment` (runtime)** : `API_ORIGIN`/`NGINX_ENVSUBST_FILTER` sont de
   vraies variables d'environnement du conteneur nginx, lues par son
   entrypoint (`docker-entrypoint.d/20-envsubst-on-templates.sh`) **à chaque
   démarrage** du conteneur pour générer `nginx.conf.template` →
   `/etc/nginx/conf.d/default.conf`. Un simple `docker compose restart frontend`
   (sans rebuild) suffit à répercuter un changement ici — voir
   [`02-nginx-csp.md`](02-nginx-csp.md).

`API_ORIGIN` a la **même valeur** que `VITE_API_BASE_URL` mais un usage
différent : il sert uniquement à injecter l'origine du backend dans le
`connect-src` de la CSP nginx (le navigateur doit pouvoir appeler une origine
distincte de celle qui sert le frontend).

## `.env` racine — comment les deux services partagent une seule clé

`.env.example` (racine) documente explicitement que `APP_API_KEY` (backend) et
`VITE_APP_API_KEY`/`args.VITE_APP_API_KEY` (frontend, ci-dessus) doivent être
la **même valeur littérale** — le fichier `.env` racine n'a d'ailleurs qu'une
seule variable `APP_API_KEY`, réutilisée deux fois dans `docker-compose.yml`
(`env_file` pour le backend, `args.VITE_APP_API_KEY: ${APP_API_KEY}` pour le
frontend) : une seule valeur à changer aux deux endroits logiques, un seul
endroit physique à éditer.

**Piège déjà rencontré sur ce projet** (documenté dans le `.env.example`
racine) : `ALLOWED_ORIGINS` (backend) doit rester synchronisé avec le port
hôte réellement publié du service `frontend` (`8021` actuellement) — un
décalage fait échouer **silencieusement** toutes les requêtes (erreur CORS
visible uniquement dans la console du navigateur, jamais dans les logs
backend). Voir [`../operations-runbook.md`](../operations-runbook.md).

## Dockerfiles — points de durcissement communs

Les deux images (`ocr-math-api/Dockerfile`, `hakili-ocr/Dockerfile`) partagent
les mêmes principes :

- **Build multi-stage** : les outils de build (compilateurs, `npm ci`, cache
  npm) ne finissent jamais dans l'image finale.
- **Utilisateur non-root** (`appuser`, uid/gid 1000) dans les deux images.
- **Image de base backend épinglée par digest** (pas seulement par tag) :
  `python:3.12.7-slim-bookworm@sha256:60d9996b...` — garantit une build
  reproductible même si le contenu derrière le tag change un jour. L'image
  frontend (`node:24.14-slim` / `nginx:1.27-alpine`) est épinglée par tag
  uniquement (pas par digest) — écart assumé, pas nécessairement à corriger
  sans réflexion (voir [`../decisions-et-limites-connues.md`](../decisions-et-limites-connues.md)).

### Backend — bibliothèques système pour WeasyPrint

```dockerfile
RUN apt-get install --no-install-recommends -y \
    libpango-1.0-0 libpangoft2-1.0-0 fonts-dejavu-core
```

Nécessaires pour que `POST /export/pdf` fonctionne sans installation
supplémentaire — voir
[`../backend/04-service-corrections-export.md`](../backend/04-service-corrections-export.md).

### Frontend — build en deux étapes

1. **`builder`** (`node:24.14-slim`) : `npm ci`, reçoit les build args
   `VITE_*`, lance `npm run build` (produit `dist/`).
2. **Image finale** (`nginx:1.27-alpine`) : copie `dist/` dans
   `/usr/share/nginx/html`, installe `nginx.conf.template` dans
   `/etc/nginx/templates/` (traité par l'entrypoint nginx au démarrage — voir
   [`02-nginx-csp.md`](02-nginx-csp.md)), ajuste les permissions pour tourner
   en non-root.

## Voir aussi

- [`02-nginx-csp.md`](02-nginx-csp.md) — la configuration nginx en détail (headers, CSP).
- [`../backend/07-configuration.md`](../backend/07-configuration.md) et [`../frontend/07-configuration.md`](../frontend/07-configuration.md) — le détail de chaque variable.
- [`../decisions-et-limites-connues.md`](../decisions-et-limites-connues.md) — pourquoi le déploiement doit rester une instance unique.
