# Client HTTP — `services/apiClient.ts` et `correctionsApi.ts`

> Dernière vérification : commit `4a30eae`.

## `apiClient.ts` — le point d'entrée réseau partagé

Toute communication avec le backend passe par ce fichier. Deux fonctions
exportées, utilisées respectivement par `useTranscribe.ts` et
`correctionsApi.ts`/`exportPdf.tsx` :

- **`fetchApi<T>(path, init)`** : `fetch` vers `API_BASE + path`, retourne du
  JSON typé, lève une `TranscribeError` normalisée en cas d'échec.
- **`fetchApiBlob(path, init)`** : identique mais pour une réponse binaire
  (le PDF généré par `POST /export/pdf`).

### `TranscribeError` — l'erreur normalisée

```typescript
class TranscribeError extends Error {
  statusCode: number;
  isRetryable: boolean;
}
```

Chaque appelant (composants, hooks) n'a jamais besoin de reparser une
`Response` brute — `throwHttpError()` classe déjà chaque code HTTP :

| Code | `isRetryable` | Particularité du message |
|---|---|---|
| 400 | non | Message du backend tel quel |
| 401 | non | Suffixé de "vérifiez VITE_APP_API_KEY dans le .env du frontend" |
| 404 | non | Message du backend tel quel |
| 422 | non | Message du backend tel quel |
| 502 | **oui** | Suffixé de "Problème de connexion à l'API Claude" |
| ≥ 500 (autre) | **oui** | Message du backend tel quel |
| échec réseau (`fetch` lève) | **oui** | "Impossible de joindre le serveur. Vérifiez que le backend est lancé sur le port 8000." |

`isRetryable` sert à l'UI pour décider si un bouton "Réessayer" a un sens —
utilisé par `LoadingScreen.tsx`.

### La clé API (`X-API-Key`)

```typescript
const API_KEY = import.meta.env.VITE_APP_API_KEY || '';
```

`buildHeaders()` pose ce header sur **chaque** requête (si `API_KEY` est
défini), en fusionnant proprement avec les headers déjà fournis par
l'appelant — **sans jamais toucher au `Content-Type` auto-généré par le
navigateur** pour un body `FormData` (multipart) : le fixer manuellement
casserait le boundary du multipart, une erreur classique et difficile à
diagnostiquer si elle est réintroduite par erreur.

**Rappel de sécurité important** (répété dans le code et dans
[`../security.md`](../security.md)) : une variable `VITE_*` est figée dans le
bundle JS au moment du build, donc **visible dans le code source livré au
navigateur** (view-source, onglet Network) — ce n'est **pas** un secret côté
client une fois déployée. Elle bloque l'appel anonyme direct à l'API, pas une
extraction volontaire par un visiteur motivé.

## `correctionsApi.ts`

Un seul export : `submitCorrection(params)`, qui construit un `FormData`
(image + champs texte) et poste à `POST /corrections` via `fetchApi`.
`SubmitCorrectionParams` est le type consommé par `useCorrectionCapture`
(voir [`03-hooks.md`](03-hooks.md)).

## Voir aussi

- [`03-hooks.md`](03-hooks.md) — `useTranscribe.ts`, le plus gros consommateur de ce client.
- [`../backend/06-utils-securite.md`](../backend/06-utils-securite.md) — le pendant backend de la vérification `X-API-Key`.
- [`07-configuration.md`](07-configuration.md) — `VITE_API_BASE_URL`/`VITE_APP_API_KEY`.
