# État global — `context/AppContext.tsx` et `types/index.ts`

> Dernière vérification : commit `4a30eae`. Code : `hakili-ocr/src/context/AppContext.tsx`, `hakili-ocr/src/types/index.ts`.

## Pourquoi un simple `useReducer` (pas Redux/Zustand)

L'app n'a qu'un seul flux d'écrans linéaire (upload → preview → loading →
result), sans navigation complexe ni état partagé entre des zones vraiment
indépendantes de l'UI. Un `useReducer` exposé via `useApp()` (`useContext`)
suffit — pas de bibliothèque de state management externe.

## `AppState` — la forme de l'état

```typescript
interface AppState {
  currentScreen: Screen;                          // 'upload' | 'preview' | 'loading' | 'result'
  uploadedImage: File | null;
  imagePreviewUrl: string | null;
  transcriptionResult: TranscriptionResult | null; // la page/l'image actuellement affichée
  selectedBlockId: number | null;
  pdfResult: PDFTranscriptionResult | null;        // non-null uniquement si un PDF est chargé
  currentPageIndex: number;
  pdfPagesTotal: number | null;                    // total réel du document, ≠ pdfResult.pages.length
}
```

**Point le plus important à comprendre avant de toucher à cet état** :
`pdfPagesTotal` (nombre total de pages du document, connu dès le premier
statut de job) est **distinct** de `pdfResult.pages.length` (nombre de pages
*déjà chargées/révélées*). Tant qu'un PDF est en cours de traitement,
`pdfResult.pages` ne contient que le **préfixe contigu** des pages prêtes
(voir `takeReadyPagePrefix` dans [`03-hooks.md`](03-hooks.md)) — les deux
valeurs ne coïncident qu'une fois le document entièrement traité.

## Les actions du reducer

| Action | Effet | Écran résultant |
|---|---|---|
| `NAVIGATE` | Change `currentScreen` directement | — |
| `SET_IMAGE` | Fichier choisi/déposé : stocke le fichier + aperçu, réinitialise l'état PDF | `preview` |
| `CONFIRM_UPLOAD` | Utilisateur valide l'aperçu (après rotation éventuelle) | `loading` |
| `CANCEL_PREVIEW` | Retour arrière depuis l'aperçu | `upload` |
| `SET_RESULT` | **Première** révélation d'un résultat (image complète, ou 1ʳᵉ page d'un PDF) | `result` |
| `MERGE_PDF_RESULT` | Pages supplémentaires d'un PDF déjà affiché (polls suivants) | reste sur `result` |
| `SET_PAGE` | Navigation entre pages d'un PDF déjà chargé | reste sur `result` |
| `SELECT_BLOCK` | Sélectionne/désélectionne un bloc | reste sur `result` |
| `UPDATE_BLOCK_MARKDOWN` | Édition du texte d'un bloc (bloc entier ou cellule recomposée) | reste sur `result` |
| `UPDATE_BLOCK_BBOX` | Déplacement d'une boîte (drag) | reste sur `result` |
| `RESET` | Retour à l'état initial ("Nouvelle image") | `upload` |

### `SET_RESULT` vs `MERGE_PDF_RESULT` — la distinction clé du PDF progressif

- **`SET_RESULT`** ne s'utilise **qu'une seule fois** par transcription : soit
  pour une image simple (payload complet en un morceau), soit pour la
  **toute première** vague de pages d'un PDF. Il bascule l'écran vers
  `'result'` et réinitialise la navigation (`currentPageIndex: 0`,
  `selectedBlockId: null`).
- **`MERGE_PDF_RESULT`** est dispatché à **chaque poll suivant**, une fois
  l'écran `'result'` déjà atteint. Il **ajoute** les pages nouvellement prêtes
  à `pdfResult.pages` sans jamais toucher `currentPageIndex`/`selectedBlockId`
  ni aux pages déjà affichées/éditées par l'utilisateur. C'est sûr par
  construction : le contenu d'une page ne change jamais une fois transcrite,
  donc un tableau de pages plus long entrant est toujours une **extension
  stricte** de ce qui est déjà affiché, jamais une réécriture conflictuelle.
  Si le nombre de pages entrant n'a pas augmenté depuis le dernier poll (aucun
  nouveau contenu), seul `pdfPagesTotal` est éventuellement mis à jour, sans
  provoquer de re-render inutile du reste de l'état.

### `UPDATE_BLOCK_MARKDOWN`/`UPDATE_BLOCK_BBOX` — duplication assumée

Les deux actions répètent la même structure de mise à jour : modifier
`transcriptionResult.blocks` **et**, si un PDF est chargé, la page courante
dans `pdfResult.pages[currentPageIndex]`. Ce n'est **pas factorisé** — assumé
tel quel pour seulement deux occurrences, pas de sur-ingénierie pour si peu de
duplication.

## `isPDFResult` — comment distinguer les deux formes de payload

```typescript
function isPDFResult(payload: TranscriptionPayload): payload is PDFTranscriptionResult {
  return 'pages' in payload;
}
```

`TranscriptionPayload` est une union (`TranscriptionResult | PDFTranscriptionResult`)
— cette garde de type (type guard) est utilisée dans `SET_RESULT` pour savoir
si le payload reçu est une image simple ou un document PDF, et brancher le
bon traitement.

## Types partagés (`types/index.ts`)

Deux familles de types dans ce fichier :

1. **Miroir des schémas backend** (`TranscriptionBlock`, `BoundingBox`,
   `OCRResult`/`TranscriptionResult`, `PageResult`, `PDFTranscriptionResult`,
   `PdfJobStartResponse`, `PdfJobStatusResponse`, etc.) — **maintenus à la
   main**, sans génération automatique depuis les schémas Pydantic backend
   (voir [`../backend/05-models-schemas.md`](../backend/05-models-schemas.md)).
   **Si vous changez un schéma Pydantic, répercutez le changement ici** — sinon
   TypeScript ne détecte rien, le décalage n'apparaît qu'au runtime.
2. **Types de l'application** (`Screen`, `AppState`, `AppAction`,
   `ExportPage`) — propres au frontend, sans équivalent backend.

`ExportPage` mérite une mention à part : `{ pageNumber: number; blocks: TranscriptionBlock[] }`,
la forme commune consommée par les deux chemins d'export (PDF et Excel — voir
[`05-utils.md`](05-utils.md)) — une seule entrée avec `pageNumber: 1` pour une
image simple, une entrée par page réelle pour un PDF.

## Voir aussi

- [`00-vue-ensemble.md`](00-vue-ensemble.md) — le diagramme des 4 écrans.
- [`02-ecrans.md`](02-ecrans.md) — quel composant dispatche quelle action.
- [`03-hooks.md`](03-hooks.md) — `useTranscribe.ts`, qui produit les payloads consommés par `SET_RESULT`/`MERGE_PDF_RESULT`.
- [`../backend/05-models-schemas.md`](../backend/05-models-schemas.md) — le schéma Pydantic source de vérité.
