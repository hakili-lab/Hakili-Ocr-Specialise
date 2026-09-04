# Configuration frontend — variables `VITE_*`

> Dernière vérification : commit `4a30eae`. Code : `hakili-ocr/.env.example`, `hakili-ocr/src/vite-env.d.ts`, `hakili-ocr/src/services/apiClient.ts`.

## Référence complète

| Variable | Défaut si absente | Rôle |
|---|---|---|
| `VITE_API_BASE_URL` | `http://127.0.0.1:8000` (repli codé dans `apiClient.ts`) | URL du backend `ocr-math-api`. |
| `VITE_APP_API_KEY` | *(vide)* | Doit être **identique** à `APP_API_KEY` côté backend — envoyée dans le header `X-API-Key` de chaque requête. |

## Particularité essentielle : ces variables sont figées au **build**, pas au runtime

Vite remplace `import.meta.env.VITE_*` par sa valeur littérale **au moment de
`vite build`** — pas au démarrage du conteneur/serveur qui sert ensuite les
fichiers statiques. Conséquences concrètes :

- **En développement local** (`npm run dev`) : lire `.env` à chaque démarrage
  du serveur Vite — un changement de `.env` nécessite de relancer `npm run dev`.
- **En Docker** : ces variables sont passées comme **build args** (voir
  `docker-compose.yml`, section `frontend.build.args`), pas comme variables
  d'environnement du conteneur au runtime. **Un build de l'image frontend est
  donc lié à un backend précis** — changer de backend (autre URL, autre
  environnement) nécessite de **reconstruire** l'image frontend, un simple
  redémarrage du conteneur ne suffit pas. Voir
  [`../deployment/01-docker-compose.md`](../deployment/01-docker-compose.md)
  pour le détail complet (y compris la distinction avec `API_ORIGIN`, qui lui
  est bien injecté au runtime, mais pour un usage différent — la CSP nginx).

## `VITE_APP_API_KEY` n'est pas un secret une fois déployé

Voir le rappel détaillé dans
[`06-services-api.md`](06-services-api.md#la-clé-api-x-api-key) et
[`../security.md`](../security.md) — toute variable `VITE_*` finit visible
dans le bundle JavaScript livré au navigateur. Cette clé bloque l'appel
anonyme et automatisé, ce n'est pas une authentification utilisateur réelle.

## `vite-env.d.ts`

Type les variables ci-dessus pour `import.meta.env` (`ImportMetaEnv`), afin
que TypeScript les connaisse et que l'autocomplétion fonctionne dans l'IDE. À
mettre à jour si une nouvelle variable `VITE_*` est ajoutée.

## Voir aussi

- [`06-services-api.md`](06-services-api.md) — comment ces variables sont consommées.
- [`../deployment/01-docker-compose.md`](../deployment/01-docker-compose.md) — build args vs runtime, en détail.
- [`../backend/07-configuration.md`](../backend/07-configuration.md) — le pendant backend (`APP_API_KEY`, `ALLOWED_ORIGINS`).
