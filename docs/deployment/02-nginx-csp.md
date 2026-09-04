# nginx et Content Security Policy (CSP)

> Dernière vérification : commit `4a30eae`. Code : `hakili-ocr/nginx.conf.template`, `hakili-ocr/Dockerfile`.

## Le mécanisme de template

`nginx.conf.template` **n'est pas** directement la configuration nginx active
— c'est un template traité par le script d'entrypoint standard de l'image
`nginx` (`/docker-entrypoint.d/20-envsubst-on-templates.sh`) **à chaque
démarrage du conteneur** :

1. Le fichier est copié dans `/etc/nginx/templates/default.conf.template`
   (par le `Dockerfile`, voir
   [`01-docker-compose.md`](01-docker-compose.md)).
2. Au démarrage, l'entrypoint applique `envsubst` dessus et écrit le résultat
   dans `/etc/nginx/conf.d/default.conf` — **le fichier réellement chargé par
   nginx**.
3. **`NGINX_ENVSUBST_FILTER=API_ORIGIN`** (variable d'environnement du
   conteneur, posée dans `docker-compose.yml`) limite la substitution à la
   **seule** variable `${API_ORIGIN}` — les variables nginx natives
   (`$uri`, `$host`, etc.) sont laissées telles quelles, pas interprétées par
   `envsubst`. **Ne retirez jamais ce filtre** sans vérifier que le fichier ne
   contient aucune variable nginx native qui serait alors accidentellement
   substituée (vide) par `envsubst`.

**Conséquence pratique** : changer `${API_ORIGIN}` (= `VITE_API_BASE_URL`) ne
nécessite **pas** de rebuild de l'image frontend — un simple
`docker compose restart frontend` (ou `up -d`) suffit, puisque l'entrypoint
régénère la config à chaque démarrage. Distinct du build-arg `VITE_API_BASE_URL`
qui, lui, est figé dans le bundle JS et **nécessite** un rebuild — voir
[`../frontend/07-configuration.md`](../frontend/07-configuration.md).

## En-têtes de sécurité

Ajoutés le 2026-08-28 (revue de sécurité, item #8), tous avec `always` (donc
appliqués aussi aux réponses d'erreur 4xx/5xx, pas seulement 2xx/3xx) :

| En-tête | Valeur | Rôle |
|---|---|---|
| `X-Content-Type-Options` | `nosniff` | Empêche le navigateur de deviner un type MIME différent de celui déclaré. |
| `X-Frame-Options` | `DENY` | Empêche d'embarquer le site dans un `<iframe>` (clickjacking). |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | Limite les informations envoyées dans l'en-tête `Referer` vers d'autres origines. |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=()` | Désactive explicitement des API navigateur non utilisées par l'app. |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` | Force HTTPS — **inerte en HTTP clair** (dev local), actif dès que le trafic public passe par une terminaison TLS en amont (reverse-proxy/CDN). |

## Content-Security-Policy (CSP)

```
default-src 'self';
script-src 'self';
style-src 'self' 'unsafe-inline' https://fonts.googleapis.com;
font-src 'self' https://fonts.gstatic.com;
img-src 'self' data: blob:;
connect-src 'self' ${API_ORIGIN} data: blob:;
worker-src 'self' blob:;
object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'
```

Chaque directive correspond à un besoin réel et documenté de l'app — **ne pas
l'assouplir sans comprendre pourquoi elle est restrictive à cet endroit** :

| Directive | Pourquoi cette valeur précise |
|---|---|
| `script-src 'self'` | Le build Vite ne produit aucun script inline — pas besoin de `'unsafe-inline'` ici. |
| `style-src 'unsafe-inline'` | Styles inline React (positions de bbox/drag calculées en JS, voir [`../frontend/04-composants-result.md`](../frontend/04-composants-result.md)) + KaTeX, qui génère des styles inline pour positionner les glyphes. |
| `style-src fonts.googleapis.com` | `@import` Google Fonts dans `src/index.css`. |
| `font-src fonts.gstatic.com` | Fichiers de police réellement servis par Google Fonts. |
| `font-src 'self'` | Polices KaTeX bundlées localement. |
| `img-src`/`connect-src data: blob:` | Aperçu d'image (`blob:`) et pages PDF encodées en base64 (`data:`), re-`fetch`ées par `cropImage.ts` (voir [`../frontend/05-utils.md`](../frontend/05-utils.md)). |
| `connect-src ${API_ORIGIN}` | Appels `fetch` vers le backend, sur une origine distincte de celle qui sert le frontend. |
| `worker-src blob:` | Web Worker `pdfjs-dist` (`usePdfPreview.ts`, voir [`../frontend/03-hooks.md`](../frontend/03-hooks.md)). |

**Si vous ajoutez une dépendance qui charge une ressource externe** (police,
script, image depuis un CDN), la CSP la bloquera silencieusement — vérifiez la
console navigateur (violations CSP explicitement listées) avant de chercher un
bug ailleurs, et étendez la directive concernée ici plutôt que d'assouplir
`default-src`.

## Statut de vérification connu

**Non encore vérifiée en conteneur réel** au moment de la dernière revue
(2026-08-28) — seulement statiquement (rendu `envsubst`, `docker compose config`,
`npm run build`). Avant de s'y fier en production : `docker compose build && up`,
puis observer la console du navigateur pour toute violation CSP en utilisant
l'app normalement (upload, PDF, export). Voir
[`../decisions-et-limites-connues.md`](../decisions-et-limites-connues.md)
pour ce point en tant qu'élément de dette assumée.

## Route spéciale pour les fichiers `.mjs`

```nginx
location ~* \.mjs$ {
    default_type application/javascript;
    try_files $uri =404;
}
```

Nécessaire pour que le Worker `pdfjs-dist` (livré en `.mjs`) soit servi avec
le bon type MIME — sans cette règle, certains navigateurs refusent d'exécuter
le module.

## Voir aussi

- [`01-docker-compose.md`](01-docker-compose.md) — build args vs runtime, en contexte.
- [`../security.md`](../security.md) — la posture de sécurité globale, dont cette CSP n'est qu'un élément.
- [`../frontend/03-hooks.md`](../frontend/03-hooks.md) — le Web Worker pdfjs concerné par `worker-src`.
