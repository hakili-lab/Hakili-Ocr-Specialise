# Posture de sécurité

> Dernière vérification : commit `4a30eae`. Revue de sécurité menée le 2026-08-13, close le 2026-08-28.

Cette page résume **ce qui est en place**, **ce qui a été délibérément écarté
et pourquoi**, et **ce qu'il ne faut pas "corriger" sans revenir sur une
décision déjà prise**. À lire avant toute modification touchant
l'authentification, le CORS, ou la gestion des erreurs.

## Ce qui est en place

### 1. Clé API partagée (`X-API-Key`)

- Header requis sur `/transcribe*`, `/corrections`, `/export/pdf` — **pas**
  sur `/` ni `/health`.
- Vérifiée par `verify_api_key()` (`app/security.py`) contre `APP_API_KEY`.
- **Refuse tout par défaut** si `APP_API_KEY` n'est pas configuré côté serveur
  (pas d'acceptation silencieuse en cas d'oubli de configuration).
- Distincte d'`ANTHROPIC_API_KEY` (backend → Anthropic).

Détail complet : [`backend/06-utils-securite.md`](backend/06-utils-securite.md).

**Limite connue et assumée** : une variable `VITE_APP_API_KEY` est figée dans
le bundle JavaScript au build — visible par quiconque inspecte le code source
livré au navigateur (view-source, onglet Network). **Ce n'est pas un secret
côté client une fois déployé.** Elle bloque l'appel anonyme et automatisé
direct à l'API, pas une extraction volontaire par un visiteur motivé. Voir
[`frontend/07-configuration.md`](frontend/07-configuration.md).

### 2. CORS verrouillé

`ALLOWED_ORIGINS` (`app/config.py`), **sans** `allow_credentials=True` —
l'authentification est entièrement basée sur un header, jamais sur un cookie,
donc pas besoin d'autoriser les credentials cross-origin. Retiré explicitement
le 2026-08-28 (item #7 de la revue).

### 3. Aucun texte d'erreur interne ne remonte au client

Toute exception `except Exception` (non prévue) est loguée **côté serveur**
avec sa trace complète, et ne renvoie au client qu'un **message générique**
plus une référence courte (`ref=xxxxxxxx`) permettant de retrouver la trace
exacte dans les logs (`log_unexpected()`, `app/utils/errors.py` — voir
[`backend/06-utils-securite.md`](backend/06-utils-securite.md)).

- Les deux 500 principaux (`transcribe_image`, `create_correction`) passent
  par `log_unexpected()`.
- Les 400 (`/pdf/start`, `/pdf/{job_id}/chunk`) renvoient un message statique.
- `job.error` (job PDF entièrement échoué) est statique.
- Les échecs par page (`_process_page_and_track`, `_process_chunk_pages`) ne
  remontent que `type(exc).__name__`, jamais le message de l'exception.

**Résidu délibéré** : `describe_anthropic_error()` (`claude_service.py`)
renvoie encore le texte brut (`str(exc)`) d'Anthropic pour les erreurs non
classées explicitement — c'est un message sur **la requête qu'on a construite**,
pas sur les internes du serveur, donc pas le même risque. Voir
[`backend/02-service-claude.md`](backend/02-service-claude.md).

**Règle à suivre pour tout nouveau code** : ne jamais renvoyer `str(exc)`
d'une exception non explicitement classée au client — toujours passer par
`log_unexpected()` ou un message statique.

### 4. Dépendances auditées et épinglées

- Backend : `requirements.txt`/`requirements-dev.txt` épinglés avec `==`,
  `pip-audit`-clean au 2026-08-28. `anthropic` volontairement gardé en
  `0.120.2` (pas `1.x`, migration majeure dédiée nécessaire). Image Docker
  épinglée par digest.
- Frontend : `npm audit`-clean. `xlsx` utilise le tarball auto-hébergé de
  SheetJS (`cdn.sheetjs.com`, intégrité vérifiée par npm) car le paquet npm
  officiel est figé sur une version `0.18.5` vulnérable.

### 5. En-têtes de sécurité + CSP (nginx)

Voir [`deployment/02-nginx-csp.md`](deployment/02-nginx-csp.md) pour le
détail complet — **statut non encore vérifié en conteneur réel** au moment de
la dernière revue, seulement statiquement.

## Ce qui a été délibérément écarté (ne pas "corriger" sans en reparler)

### Pas de limitation de débit (rate limiting)

**Décision explicite de l'utilisateur** (2026-08-28) : "c'est une application
privée donc c'est un risque que nous pouvons prendre." Le vecteur "extraire la
clé baked-in dans le bundle → matraquer `/transcribe` → faire exploser la
facture Anthropic" est jugé acceptable pour un déploiement privé à une
audience connue.

**Le vrai filet de sécurité est en dehors du code** : une alerte de budget/dépense
configurée directement sur le compte Anthropic. **Vérifiez qu'elle existe
toujours** avant de considérer ce risque comme couvert — rien dans le code ne
la garantit.

**Revoir cette décision si** : l'app s'ouvre à un public plus large ou moins
maîtrisé.

### Comparaison de clé API non constant-time

`app/security.py` utilise `provided_key != settings.APP_API_KEY` (comparaison
Python standard), pas `secrets.compare_digest()` (constant-time, qui protège
contre une attaque par mesure du temps de réponse pour deviner la clé
caractère par caractère).

**Décision explicite de l'utilisateur** (2026-08-28) : "elle n'est pas grave
dans notre cas." Le signal de timing exploitable est de l'ordre de la
nanoseconde, noyé par la latence réseau normale, et la clé n'est de toute
façon pas un vrai secret une fois le frontend déployé (voir plus haut).

**Le correctif, si jamais revisité, est un changement de 2 lignes** dans
`verify_api_key()` — pas un chantier.

## Convention à suivre pour tout nouveau endpoint

1. Protégez-le avec `Depends(verify_api_key)` s'il expose une action non
   publique (à l'image des 3 routers existants).
2. Tout `except Exception` qui pourrait laisser fuiter un message d'exception
   non prévu doit passer par `log_unexpected()`.
3. Toute nouvelle variable d'environnement sensible doit avoir un défaut vide
   plutôt qu'une valeur de démo qui pourrait finir en production par oubli.
4. Si le endpoint introduit un nouvel appel réseau externe, pensez au
   sémaphore/à la protection contre l'abus (voir
   [`backend/02-service-claude.md`](backend/02-service-claude.md) pour le
   modèle existant côté Anthropic).

## Voir aussi

- [`backend/06-utils-securite.md`](backend/06-utils-securite.md) — le code exact derrière la clé API et l'assainissement des erreurs.
- [`deployment/02-nginx-csp.md`](deployment/02-nginx-csp.md) — la CSP et les en-têtes en détail.
- [`decisions-et-limites-connues.md`](decisions-et-limites-connues.md) — le contexte de chaque décision de sécurité, avec date et raisonnement complet.
- [`operations-runbook.md`](operations-runbook.md) — que faire face à un incident de sécurité suspecté (clé compromise, facture Anthropic anormale).
