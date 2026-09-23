/**
 * services/apiClient.ts
 * Client HTTP partagé vers `ocr-math-api` : point d'entrée unique (`fetchApi`)
 * réutilisé par `useTranscribe.ts` et `correctionsApi.ts`, qui normalise les
 * erreurs réseau/HTTP en `TranscribeError` typée (code, message lisible,
 * retryable ou non) plutôt que de laisser chaque appelant reparser `Response`.
 */

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000';

// Envoyée dans le header X-API-Key (voir fetchApi) — doit être identique à
// APP_API_KEY côté backend (app/security.py). Rappel : une variable VITE_*
// est visible dans le bundle JS livré au navigateur une fois buildée, ce
// n'est donc pas un secret côté client — voir README.md § Configuration.
const API_KEY = import.meta.env.VITE_APP_API_KEY || '';

/** Erreur réseau/HTTP normalisée, portée par `statusCode` et `isRetryable` pour que l'UI sache si "Réessayer" a un sens. */
export class TranscribeError extends Error {
  statusCode: number;
  isRetryable: boolean;

  constructor(message: string, statusCode: number, isRetryable: boolean = false) {
    super(message);
    this.statusCode = statusCode;
    this.isRetryable = isRetryable;
    this.name = 'TranscribeError';
  }
}

/**
 * Message de repli quand la réponse d'erreur n'a pas de `detail` JSON exploitable (ex. le
 * backend est injoignable et une infrastructure intermédiaire — proxy, plateforme d'hébergement
 * — renvoie sa propre page d'erreur générique). Jamais le code HTTP brut affiché tel quel
 * (`"Erreur 429"`, `"Erreur 500"`...) : ce n'est pas compréhensible sans connaissance technique,
 * et le vrai détail (quand le backend en fournit un) prend de toute façon le dessus juste en
 * dessous via `errorData.detail`.
 */
function defaultMessageForStatus(status: number): string {
  if (status === 404) return 'Ressource introuvable.';
  if (status === 422) return 'Les données envoyées ne sont pas valides.';
  if (status === 429) return 'Trop de demandes en même temps. Réessayez dans quelques instants.';
  if (status >= 500) return 'Le serveur a rencontré un problème. Réessayez dans quelques instants.';
  return 'Une erreur est survenue. Réessayez.';
}

/** Lève une `TranscribeError` classée par code HTTP à partir d'une `Response` en échec — partagé par `fetchApi`/`fetchApiBlob`. */
async function throwHttpError(response: Response): Promise<never> {
  let errorMessage = defaultMessageForStatus(response.status);
  try {
    const errorData = await response.json();
    if (errorData.detail) errorMessage = errorData.detail;
  } catch {
    const text = await response.text().catch(() => '');
    // Un corps texte brut (page d'erreur HTML d'un proxy, etc.) n'est pas plus lisible que le
    // message de repli — on ne le montre que s'il ressemble à une phrase courte, jamais un blob.
    if (text && text.length < 200 && !text.trimStart().startsWith('<')) errorMessage = text;
  }

  if (response.status === 400) throw new TranscribeError(errorMessage, 400, false);
  if (response.status === 401) throw new TranscribeError(`${errorMessage} — vérifiez VITE_APP_API_KEY dans le .env du frontend.`, 401, false);
  if (response.status === 404) throw new TranscribeError(errorMessage, 404, false);
  if (response.status === 422) throw new TranscribeError(errorMessage, 422, false);
  if (response.status === 502) throw new TranscribeError(`${errorMessage} — Problème de connexion à l'API Claude.`, 502, true);
  if (response.status >= 500) throw new TranscribeError(errorMessage, response.status, true);
  throw new TranscribeError(errorMessage, response.status, false);
}

/** Convertit une `Response` en JSON typé, ou lève une `TranscribeError` classée par code HTTP. */
async function parseJsonOrThrow<T>(response: Response): Promise<T> {
  if (!response.ok) await throwHttpError(response);

  try {
    return (await response.json()) as T;
  } catch {
    throw new TranscribeError("La réponse du serveur n'est pas un JSON valide.", 200, false);
  }
}

/** `Headers` avec le header `X-API-Key` déjà posé — partagé par `fetchApi`/`fetchApiBlob`. Fusionne proprement avec les headers déjà fournis par l'appelant sans jamais toucher au Content-Type auto-généré par le navigateur pour un body FormData (multipart) — le fixer ici casserait le boundary. */
function buildHeaders(init?: RequestInit): Headers {
  const headers = new Headers(init?.headers);
  if (API_KEY) headers.set('X-API-Key', API_KEY);
  return headers;
}

async function doFetch(path: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(`${API_BASE}${path}`, { ...init, headers: buildHeaders(init) });
  } catch {
    throw new TranscribeError(
      'Impossible de joindre le serveur. Vérifiez que le backend est lancé sur le port 8000.',
      0,
      true
    );
  }
}

/** `fetch` vers `API_BASE + path`, avec parsing/erreurs déjà normalisés — voir `parseJsonOrThrow`. */
export async function fetchApi<T>(path: string, init?: RequestInit): Promise<T> {
  return parseJsonOrThrow<T>(await doFetch(path, init));
}

/**
 * Requête "best effort" destinée à survivre à la fermeture de la page (ex. annuler un job
 * PDF quand l'utilisateur ferme l'onglet, voir `useTranscribe.ts`) — `fetch(..., { keepalive:
 * true })` plutôt que `navigator.sendBeacon` : `sendBeacon` ne permet pas d'ajouter de headers
 * personnalisés, or le backend exige `X-API-Key` sur cette route ; `keepalive` le permet tout
 * en offrant la même garantie de survie à la fermeture de l'onglet. Échec réseau ignoré
 * délibérément (pas de retry, pas de throw) : la page est de toute façon en train de se
 * fermer, personne ne verra un message d'erreur.
 */
export function sendKeepaliveRequest(path: string): void {
  fetch(`${API_BASE}${path}`, { method: 'POST', keepalive: true, headers: buildHeaders() }).catch(() => {});
}

/** Comme `fetchApi`, mais pour une réponse binaire (ex. le PDF généré par `POST /export/pdf`) plutôt que du JSON. */
export async function fetchApiBlob(path: string, init?: RequestInit): Promise<Blob> {
  const response = await doFetch(path, init);
  if (!response.ok) await throwHttpError(response);
  return response.blob();
}
