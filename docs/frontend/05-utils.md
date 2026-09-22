# Utilitaires purs — `src/utils/*`

> Dernière vérification : commit `4a30eae`.

Toutes les fonctions décrites ici sont **pures** (pas de dépendance React) —
faciles à tester isolément si des tests sont ajoutés un jour.

## `tableMarkdown.ts` — parsing des blocs-tableau

Le backend émet un bloc par ligne de tableau (voir
[`../backend/02-service-claude.md`](../backend/02-service-claude.md)). Ce
module fait le lien entre cette convention et le rendu/l'édition frontend.

- **`tableLines(markdown)`** : lignes non vides d'un bloc → `[header, séparateur, ligne de données]`.
- **`isTableBlockMarkdown(markdown)`** : vrai si toutes les lignes commencent par `|`.
- **`parseTableRowCells(rowLine)`** : découpe une ligne en cellules,
  **consciente des formules LaTeX** — un `|` à l'intérieur d'un span
  `$...$`/`$$...$$` (ex. `$P(A|B)=0.5$`, `$|x|$`) n'est **pas** un séparateur
  de colonne. Un simple `split('|')` couperait la cellule en deux fragments
  avec un `$` non apparié dans chacun. Un `\` échappe le caractère suivant.
- **`withReplacedCell(rowLine, column, newValue)`** : recompose une ligne avec
  une cellule remplacée — utilisé après validation d'une édition de cellule
  (`useCellEditing`).
- **`getRowCellTexts(markdown)`** : cellules de la ligne de données (3ᵉ ligne)
  — ce qu'affiche/édite `TableRow.tsx`.
- **`insertPageAndRowColumns(cells, page, row)`** : insère les colonnes
  "Page"/"Ligne" juste après "Confiance" (position 0, injectée côté backend).
  Ces deux colonnes sont **calculées côté frontend** et n'existent **jamais**
  dans `block.markdown` — ni `parseTableRowCells` ni `getRowCellTexts` ne les
  connaissent ; elles ne sont ajoutées qu'à l'affichage/l'export, jamais à la
  donnée éditable.
- **`groupBlocksForRender(blocks)`** : regroupe les blocs-tableau consécutifs
  en un seul groupe `{ kind: 'table', blocks: [...] }` — les blocs non-tableau
  restent `{ kind: 'single', block }`. Utilisé par `ContentPanel.tsx`,
  `TranscriptExport.tsx` et `exportExcel.ts`.

## `markdownHighlight.ts` — le système de surbrillance `==...==`

Rend le marqueur "contenu incertain" du backend en surbrillance visuelle, dans
**deux contextes distincts** qui nécessitent des mécanismes différents :

1. **Hors formule** : `remarkRehypeOptions` (un handler pour le plugin
   `remark-highlight-mark`) convertit `==mot==` en `<mark class="ocr-uncertain">`.
   **Pourquoi pas une simple regex sur le texte** : dès que le contenu
   contient une formule `$...$`, `remark-math` a déjà découpé le texte en
   plusieurs nœuds AST avant qu'une regex texte ne puisse voir les deux `==`
   ensemble.
2. **À l'intérieur d'une formule** (`$...$`/`$$...$$`) : `remark-math` traite
   tout le contenu comme un bloc LaTeX opaque, donc `remark-highlight-mark` ne
   voit jamais les `==` placés là. `preprocessMathHighlights()` **pré-traite le
   Markdown brut avant** `ReactMarkdown` : repère chaque span mathématique et y
   convertit `==...==` en `\colorbox{#FDE3B8}{...}` — une commande KaTeX
   native — pour obtenir un surlignage visuel équivalent au `<mark>` utilisé
   hors formule.

**Ne jamais appliquer ce prétraitement à `editDraft`/`cellDraft`** — l'édition
doit toujours montrer le Markdown/LaTeX brut, jamais la version transformée.

- **`stripUncertainMarkers(markdown)`** : pour les exports (PDF/Excel) — un
  simple retrait global des `==...==`, en ne gardant que le texte intérieur.
  Contrairement à `preprocessMathHighlights`, pas besoin ici de distinguer
  l'intérieur d'une formule : le résultat voulu est le même dans les deux cas.

## `geometry.ts` — mathématiques du drag de bbox

Deux fonctions pures utilisées par `useBlockDrag` :

- **`pixelDeltaToNormalized(deltaXPx, deltaYPx, containerWidthPx, containerHeightPx)`** :
  convertit un déplacement écran en pixels vers une fraction `[0,1]`, à
  l'échelle du **conteneur** qui sert de référentiel de positionnement des
  boîtes (le wrapper `relative` autour de l'image) — la même échelle que
  `BoundingBox`.
- **`translateBBox(bbox, dx, dy)`** : translate une bbox en conservant sa
  largeur/hauteur. Le delta est **réduit au maximum possible** (`clampAxisDelta`)
  si la translation demandée pousserait un bord hors du cadre `[0,1]` — la
  boîte s'arrête donc net au bord de l'image plutôt que d'être tronquée ou
  redimensionnée involontairement.

## `confidenceColors.ts` — couleurs par palier de confiance

Trois fonctions (`getConfidenceColor`, `getConfidenceBorder`,
`getConfidenceBoxBg`), toutes sur les mêmes trois paliers : `≥60` fiable
(vert), `≥30` à vérifier (orange), sinon incertain (rouge). Utilisées à la
fois pour le texte des blocs et les boîtes sur l'image — **modifier les seuils
ici les change partout dans l'app d'un coup**.

## `cropImage.ts` — découpe d'image pour les corrections

**`cropImageToBlob(imageSrc, bbox)`** : `fetch` + `createImageBitmap` + crop
sur `<canvas>`. Fonctionne aussi bien pour un `blob:` (image simple) qu'un
`data:` (page PDF encodée en base64) — même mécanisme des deux côtés. Utilisé
uniquement par `useCorrectionCapture`.

## `fileTransform.ts` — rotation réelle du fichier

- **`rotateImageFile(file, angle)`** : redessine l'image pivotée sur un
  `<canvas>` hors-écran (permute largeur/hauteur du canvas pour 90°/270°).
- **`rotatePdfFile(file, rotations)`** : écrit la rotation dans la métadonnée
  `/Rotate` de chaque page via `pdf-lib`, **additionnée** à une rotation déjà
  présente (jamais remplacée) — respectée par PyMuPDF côté backend, sans
  re-rasteriser le contenu. Met en cache le `PDFDocument` déjà chargé/muté via
  `cachePdfDoc()` (voir `pdfDocCache.ts` ci-dessous), pour que le flux d'envoi
  qui suit n'ait pas besoin de reparser les mêmes octets.
- **`applyRotation(file, rotation, isPdf)`** : point d'entrée unique appelé
  par `PreviewScreen`, délègue vers l'une des deux fonctions ci-dessus.

## `pdfChunking.ts` — découpage d'un PDF pour l'upload par morceaux

Voir [`../architecture/04-flux-pdf-chunke.md`](../architecture/04-flux-pdf-chunke.md)
pour le contexte complet. Fonctions clés :

- **`PDF_CHUNK_PAGE_COUNT_THRESHOLD = 10`** : au-delà, le flux chunké
  remplace le flux classique.
- **`PDF_CHUNK_SIZE_PAGES = 6`** (test local — `10` en production, voir
  `docs/decisions-et-limites-connues.md`) : pages par morceau, au-dessus
  d'`ANTHROPIC_CONCURRENCY` (3 en test local / 2 en production, backend) pour
  qu'un morceau sature le sémaphore de traitement pendant que le suivant
  s'envoie.
- **`loadPdf(file)`** : charge le PDF (`PDFDocument.load`, coûteux sur un gros
  fichier) et rapporte son nombre de pages. Réutilise le `PDFDocument` déjà
  en cache (`takeCachedPdfDoc`) si `file` est le fichier pivoté produit par
  `rotatePdfFile` — évite de reparser deux fois les mêmes octets.
- **`splitLoadedPdfIntoChunks(doc, pagesPerChunk)`** : copie structurelle des
  pages (`copyPages`/`addPage`), **aucun rendu de pixel** — le seul
  rasteriseur du projet reste PyMuPDF côté serveur.

## `pdfDocCache.ts` — cache `File` → `PDFDocument`

```typescript
const cache = new WeakMap<File, PDFDocument>();
```

Un `WeakMap` (pas un cache manuel avec invalidation explicite) : la clé (le
`File` produit par `rotatePdfFile`) n'est jamais réutilisée après le flux
d'envoi — l'entrée disparaît d'elle-même une fois le `File` hors de portée,
rien à nettoyer.

## `exportPdf.tsx` / `exportExcel.ts` / `exportSanitize.ts` — les deux exports

- **`exportSanitize.ts`** — `sanitizeExportPages()` : retire les marqueurs
  `==...==` de chaque bloc **avant tout export**, partagé par les deux
  chemins ci-dessous.
- **`exportPdf.tsx`** — `exportTranscriptionToPdf()` : monte `TranscriptExport`
  hors-écran, attend deux `requestAnimationFrame` pour que KaTeX ait peint,
  sérialise `.innerHTML`, poste au backend (`POST /export/pdf`), télécharge le
  blob PDF renvoyé. Voir
  [`../backend/04-service-corrections-export.md`](../backend/04-service-corrections-export.md).
- **`exportExcel.ts`** — `exportTranscriptionToExcel()` : construit un
  classeur `.xlsx` **entièrement côté client** (SheetJS), une feuille par page
  source. Les groupes de blocs-tableau redeviennent de vrais tableaux Excel
  (en-tête + une ligne par bloc) ; un bloc simple devient une ligne de texte
  libre. Le Markdown/LaTeX est écrit **tel quel** (Excel ne rend aucune
  formule — `$x^2$` reste littéralement `$x^2$`).
  **`xlsx` est importé dynamiquement** (`await import('xlsx')`) plutôt qu'en
  haut du fichier — c'est une bibliothèque volumineuse qui, importée
  statiquement, finirait dans le bundle principal et serait téléchargée par
  chaque visiteur même s'il n'exporte jamais en Excel. Vite la sépare
  automatiquement dans son propre chunk chargé à la demande.

## Voir aussi

- [`03-hooks.md`](03-hooks.md) — les hooks qui appellent ces fonctions.
- [`04-composants-result.md`](04-composants-result.md) — les composants qui consomment `tableMarkdown.ts`/`markdownHighlight.ts`.
- [`../backend/04-service-corrections-export.md`](../backend/04-service-corrections-export.md) — le pendant backend de l'export PDF.
