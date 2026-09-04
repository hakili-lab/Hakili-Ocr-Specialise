# Documentation Hakili OCR

Documentation technique complète du projet, écrite pour permettre à un
développeur qui n'a jamais touché le code de : comprendre l'architecture,
modifier n'importe quelle partie sans casser le reste, et diagnostiquer
rapidement une panne en production.

> **Dernière vérification par rapport au code : commit `4a30eae` (2026-09-04).**
> Chaque fichier porte cette même date en en-tête — si le dépôt a beaucoup
> évolué depuis, vérifiez le code réel avant de vous fier à un détail précis
> (nom de fonction, ligne, valeur par défaut).

> **Note sur `CLAUDE.md`** : ce dépôt contient aussi un fichier `CLAUDE.md` à
> la racine, destiné à guider un assistant IA (Claude Code) travaillant sur ce
> projet — c'est un document de travail interne, pas la documentation
> officielle du projet. **`docs/` (ce dossier) est la référence à jour destinée
> aux développeurs humains** ; les deux documents ne sont pas garantis rester
> synchronisés mot pour mot.

## Par où commencer

| Votre situation | Allez ici |
|---|---|
| Je découvre le projet, je veux une vue d'ensemble | [`architecture/01-vue-ensemble.md`](architecture/01-vue-ensemble.md) |
| Je veux comprendre comment une transcription se déroule | [`architecture/02-flux-image.md`](architecture/02-flux-image.md) → [`04-flux-pdf-chunke.md`](architecture/04-flux-pdf-chunke.md) |
| Je vais travailler sur le backend | [`backend/00-vue-ensemble.md`](backend/00-vue-ensemble.md) |
| Je vais travailler sur le frontend | [`frontend/00-vue-ensemble.md`](frontend/00-vue-ensemble.md) |
| Je dois déployer ou changer la config Docker/nginx | [`deployment/01-docker-compose.md`](deployment/01-docker-compose.md) |
| Il y a une panne en production, je dois diagnostiquer vite | [`operations-runbook.md`](operations-runbook.md) |
| Je m'apprête à "corriger" quelque chose qui semble bizarre | [`decisions-et-limites-connues.md`](decisions-et-limites-connues.md) — vérifiez que ce n'est pas déjà un choix assumé |
| Question de sécurité / authentification | [`security.md`](security.md) |

## Sommaire complet

### Architecture (le fonctionnement d'ensemble)

1. [Vue d'ensemble](architecture/01-vue-ensemble.md) — ce que fait l'app, les deux projets, comment ils communiquent
2. [Flux : image simple](architecture/02-flux-image.md) — `POST /transcribe`, pas à pas
3. [Flux : PDF classique](architecture/03-flux-pdf.md) — job asynchrone, polling, affichage progressif
4. [Flux : PDF par morceaux](architecture/04-flux-pdf-chunke.md) — pour les gros documents (> 30 pages)

### Backend (`ocr-math-api/`)

0. [Vue d'ensemble](backend/00-vue-ensemble.md) — stack, arborescence, démarrage, tests
1. [Routers](backend/01-routers.md) — tous les endpoints HTTP
2. [Service Claude](backend/02-service-claude.md) — prompt, retry/backoff, sémaphore, cache
3. [Jobs PDF](backend/03-service-jobs-pdf.md) — le store en mémoire et ses limites
4. [Corrections et export PDF](backend/04-service-corrections-export.md) — SQLite, WeasyPrint
5. [Schémas Pydantic](backend/05-models-schemas.md) — le contrat de données
6. [Utilitaires et sécurité](backend/06-utils-securite.md) — images, PDF, clé API, erreurs
7. [Configuration](backend/07-configuration.md) — toutes les variables d'environnement

### Frontend (`hakili-ocr/`)

0. [Vue d'ensemble](frontend/00-vue-ensemble.md) — stack, arborescence, démarrage
1. [État global](frontend/01-etat-global.md) — `AppContext`, le reducer, les types partagés
2. [Les 4 écrans](frontend/02-ecrans.md) — ce que voit l'utilisateur, étape par étape
3. [Hooks](frontend/03-hooks.md) — toute la logique réseau et d'édition
4. [Composants de l'écran Résultat](frontend/04-composants-result.md) — image annotée, tableaux, drag
5. [Utilitaires](frontend/05-utils.md) — parsing markdown, géométrie, exports
6. [Client API](frontend/06-services-api.md) — le fetch partagé vers le backend
7. [Configuration](frontend/07-configuration.md) — les variables `VITE_*`

### Déploiement

1. [Docker et docker-compose](deployment/01-docker-compose.md)
2. [nginx et Content Security Policy](deployment/02-nginx-csp.md)

### Transverse

- [Sécurité](security.md) — posture complète, ce qui est en place et ce qui est délibérément écarté
- [Runbook opérationnel](operations-runbook.md) — diagnostiquer une panne en production, lisible seul
- [Décisions et limites connues](decisions-et-limites-connues.md) — pourquoi chaque choix structurant a été fait

## Convention utilisée dans ces documents

- Chaque fichier commence par une ligne `> Dernière vérification : commit ...`
  et cite les fichiers de code exacts qu'il décrit.
- Une section "Voir aussi" en fin de fichier relie les documents entre eux —
  suivez ces liens plutôt que de dupliquer l'information ailleurs.
- Le "pourquoi" est privilégié sur le "quoi" partout où le code source
  suffirait déjà à répondre à "quoi" — l'objectif est qu'un changement se
  fasse en connaissance de cause, pas seulement qu'il compile.
