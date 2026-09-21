# Runbook — diagnostiquer une panne en production

> Dernière vérification : commit `4a30eae`. Ce fichier est pensé pour être lu **seul**, sans avoir à naviguer dans le reste de la documentation — chaque section renvoie vers le détail si besoin, mais l'essentiel du diagnostic tient ici.

## Où regarder en premier

```bash
docker compose logs -f backend    # logs applicatifs (FastAPI, uvicorn, Python)
docker compose logs -f frontend   # logs nginx (accès, erreurs de config)
docker compose ps                 # les deux services sont-ils "Up" ?
```

Le format de log backend est `%(asctime)s - %(name)s - %(levelname)s - %(message)s`
(`app/main.py`). Une erreur inattendue (`logger.exception`) inclut la trace
complète **avec un identifiant court** — voir la section suivante.

## "L'utilisateur voit un message générique avec une référence" (ex. `ref=a1b2c3d4`)

C'est le comportement voulu (voir [`security.md`](security.md)) : le client
ne voit jamais le détail d'une exception inattendue. Pour retrouver la cause
exacte :

```bash
docker compose logs backend | grep "ref=a1b2c3d4"
```

Le log correspondant contient la trace Python complète (`logger.exception`,
via `app/utils/errors.py:log_unexpected`). Les endroits qui génèrent ce genre
de référence : `transcribe_image` (500 inattendu) et `create_correction` (500
inattendu). Voir [`backend/06-utils-securite.md`](backend/06-utils-securite.md).

## "Le frontend n'arrive pas à joindre le backend"

Ordre de vérification, du plus fréquent au moins fréquent :

1. **`ALLOWED_ORIGINS` (backend) ne correspond pas à l'origine réelle du
   frontend.** Symptôme : erreur CORS visible **uniquement dans la console du
   navigateur** (jamais dans les logs backend — la requête n'atteint même pas
   le handler). Vérifiez `ALLOWED_ORIGINS` dans le `.env` du backend contre le
   port hôte réellement publié du frontend (`docker-compose.yml`, actuellement
   `8021`). **Déjà arrivé une fois sur ce projet** — voir
   [`backend/07-configuration.md`](backend/07-configuration.md).
2. **`VITE_API_BASE_URL` pointe vers la mauvaise URL.** Rappel : cette
   variable est **figée dans le bundle au build** côté frontend — un
   changement de `.env` seul ne suffit pas, il faut reconstruire l'image
   frontend (`docker compose build frontend`). Voir
   [`frontend/07-configuration.md`](frontend/07-configuration.md).
3. **`APP_API_KEY` (backend) ≠ `VITE_APP_API_KEY` (frontend).** Symptôme :
   401 systématique sur tous les endpoints protégés. Le message d'erreur
   frontend le rappelle explicitement ("vérifiez VITE_APP_API_KEY dans le .env
   du frontend").
4. **(Windows + Docker Desktop + WSL2 uniquement) `localhost` ne répond pas
   mais `127.0.0.1` fonctionne.** Un processus `wslrelay.exe` resté d'une
   session Docker précédente peut squatter `[::1]:<port>`, masquant le vrai
   proxy Docker lié sur `0.0.0.0:<port>`. Diagnostic :
   ```bash
   netstat -ano | grep <port>       # cherchez DEUX PID différents sur le même port
   tasklist //FI "PID eq <pid>"     # confirmez lequel est wslrelay.exe
   taskkill //F //PID <pid-wslrelay>
   ```
   Si insuffisant : `wsl --shutdown` puis relancer Docker Desktop (plus
   disruptif — coupe tous les WSL, pas seulement ce projet).

## "Une transcription échoue avec un message lié à Anthropic"

Le message affiché à l'utilisateur est déjà classé par
`describe_anthropic_error()` (voir
[`backend/02-service-claude.md`](backend/02-service-claude.md)) :

| Message affiché | Cause probable | Action |
|---|---|---|
| "crédits insuffisants" | Solde du compte Anthropic épuisé | Recharger le compte sur console.anthropic.com |
| "clé API Anthropic invalide ou expirée" | `ANTHROPIC_API_KEY` mal configurée | Vérifier la variable, régénérer une clé si besoin |
| "service actuellement surchargé" (429) | Rate limit Anthropic atteint | Vérifier si `ANTHROPIC_CONCURRENCY` est trop élevé pour le tier du compte, ou attendre |
| "temporairement indisponible côté Anthropic" (5xx/overloaded) | Incident côté Anthropic | Vérifier status.anthropic.com, réessayer plus tard |
| "impossible de contacter le service" | Problème réseau sortant / timeout | Vérifier la connectivité sortante du serveur, `ANTHROPIC_REQUEST_TIMEOUT_SECONDS` |
| "document trop volumineux" (413) | Image/page trop lourde pour l'API | Vérifier `resize_for_vision` fonctionne bien, ou réduire la résolution PDF (`convert_pdf_to_images`, dpi) |

Pour une erreur non classée (message brut d'Anthropic affiché tel quel), c'est
volontaire — voir [`security.md`](security.md).

## "Un job PDF reste bloqué en `processing` indéfiniment"

1. Vérifiez d'abord si le job est **réellement** bloqué ou juste long : un PDF
   de plusieurs centaines de pages peut légitimement prendre plusieurs
   minutes, borné par `ANTHROPIC_CONCURRENCY` (2 appels simultanés par
   défaut).
2. Si c'est un job **chunké** (`/pdf/start-chunked`) et que le client a
   abandonné l'upload en cours de route (onglet fermé), le job sera purgé
   automatiquement après `JOB_STALL_TIMEOUT_SECONDS` (défaut 30 min) — pas
   d'action nécessaire, juste patienter ou attendre la prochaine création de
   job (la purge se déclenche à cette occasion, pas via une tâche planifiée
   séparée). Voir [`backend/03-service-jobs-pdf.md`](backend/03-service-jobs-pdf.md).
3. Si le backend a **redémarré** pendant le traitement (déploiement, crash),
   le job est **définitivement perdu** — c'est la limite structurelle du
   store en mémoire (`job_store.py`). Le frontend recevra un 404 sur le
   prochain poll. Il n'y a rien à récupérer : l'utilisateur doit relancer la
   transcription.
4. Si un log `"Échec inattendu du job PDF %s"` apparaît, cherchez la trace
   complète juste au-dessus dans les logs (pas de `ref=` ici, message direct
   dans `_run_pdf_job`).

## "L'export PDF échoue" (500 sur `POST /export/pdf`)

- **En Docker** : ne devrait normalement pas arriver, les bibliothèques
  WeasyPrint (`libpango-1.0-0`, etc.) sont installées dans l'image. Vérifiez
  les logs pour une erreur `cairo`/`pango`/`gobject` malgré tout.
- **Hors Docker (dev local, notamment Windows)** : WeasyPrint nécessite le
  runtime GTK3/Pango installé **séparément** sur la machine — absent par
  défaut. Tous les autres endpoints continuent de fonctionner normalement (cf.
  import différé, [`backend/04-service-corrections-export.md`](backend/04-service-corrections-export.md)).
  Ce n'est **pas** un bug de code, c'est un prérequis d'environnement.

## "Des corrections échouent à s'enregistrer" / "database is locked"

Symptôme SQLite classique sous forte concurrence d'écriture. Comme les
corrections sont une action à faible fréquence (voir
[`backend/04-service-corrections-export.md`](backend/04-service-corrections-export.md)),
ce n'est attendu qu'à un volume significatif. **Le correctif à faible coût**
est d'activer `PRAGMA journal_mode=WAL` sur la base SQLite — **pas** une
migration vers un autre SGBD. Voir
[`decisions-et-limites-connues.md`](decisions-et-limites-connues.md).

## "Des ressources semblent bloquées par la CSP" (police, script, image ne charge pas)

Ouvrez la console du navigateur — Chrome/Firefox affichent explicitement
chaque violation CSP avec la directive concernée. Étendez la directive
correspondante dans `nginx.conf.template`, ne relâchez jamais `default-src`
globalement. Voir [`deployment/02-nginx-csp.md`](deployment/02-nginx-csp.md)
pour la CSP actuelle et sa justification directive par directive.

## "La facture Anthropic augmente anormalement"

1. Vérifiez qu'un **spend alert** est bien actif sur le compte Anthropic
   (backstop externe au code — voir [`security.md`](security.md), aucune
   limitation de débit n'est implémentée côté application par choix assumé).
2. Vérifiez `ANTHROPIC_CONCURRENCY` : une valeur trop élevée peut multiplier
   le débit d'appels sans qu'il y ait d'abus réel.
3. Suspectez une clé `VITE_APP_API_KEY` extraite du bundle et utilisée pour
   appeler directement l'API — pas détectable dans les logs applicatifs
   actuels (pas de rate limiting ni de tracking par IP), seul l'historique de
   facturation Anthropic donnera un signal (pic de volume).

## Ressources externes à surveiller

- **status.anthropic.com** — incidents côté fournisseur du service OCR.
- **Compte Anthropic (console.anthropic.com)** — solde de crédits, spend
  alert, historique de facturation.

## Voir aussi

- [`security.md`](security.md) — la politique complète derrière les messages d'erreur et l'authentification.
- [`decisions-et-limites-connues.md`](decisions-et-limites-connues.md) — le contexte de chaque limite structurelle mentionnée ici.
- [`backend/02-service-claude.md`](backend/02-service-claude.md) — le détail technique du retry/backoff Anthropic.
