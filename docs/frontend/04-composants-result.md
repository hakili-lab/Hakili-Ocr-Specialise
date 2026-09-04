# Composants de l'écran Résultat

> Dernière vérification : commit `4a30eae`. Code : `hakili-ocr/src/components/result/*.tsx`, `hakili-ocr/src/components/ResultScreen.tsx`.

`ResultScreen.tsx` est un pur orchestrateur : il instancie les hooks (voir
[`03-hooks.md`](03-hooks.md)) et compose les panneaux décrits ici. Il garde en
propre uniquement ce qui est partagé entre panneaux : les refs miroir
(`blockRefs`, `blocksRef`), `imageContainerRef`, et les deux providers de
contexte (`EditingCellContext`/`CellDraftContext`).

## `ResultHeader.tsx`

Purement présentationnel (aucun état propre) : logo, navigation de pages PDF
(désactivée au-delà de `pagesLoaded`, distinct de `totalPages` — voir
[`01-etat-global.md`](01-etat-global.md)), indicateur "Transcription en
cours…" pulsant quand `isStreaming`, boutons d'export (désactivés pendant le
streaming pour ne jamais exporter un document incomplet).

## `AnnotatedImagePanel.tsx` — colonne gauche

Affiche l'image source + la légende de confiance (3 pastilles de couleur) +
les boîtes de blocs (`BlockOverlay`). Pose les deux providers
`DraggingBlockContext`/`DragOffsetContext` consommés par `BlockOverlay` —
l'état de drag lui-même vit dans `useBlockDrag` (instancié par
`ResultScreen`), ce composant ne fait que le distribuer.

## `ContentPanel.tsx` — colonne droite

Rendu Markdown/LaTeX des blocs, avec édition en place. Utilise
`groupBlocksForRender()` (voir [`05-utils.md`](05-utils.md)) pour regrouper
les blocs-ligne de tableau consécutifs en un seul `<table>` visuel — chaque
ligne reste un `<TableDataRow>` indépendant (voir `TableRow.tsx` ci-dessous).

Un bloc **simple** (hors tableau) s'édite en entier via une `<textarea>` ; les
boutons Valider/Annuler apparaissent en overlay dans le coin. Un bloc **table**
se rend comme une vraie balise `<table>` HTML, avec les colonnes "Page"/"Ligne"
insérées après "Confiance" (`insertPageAndRowColumns`).

## `BlockOverlay.tsx` — une boîte bbox sur l'image

Mémoïsé (`React.memo`) et keyed sur `block.id`, pour que déplacer **une**
boîte ne re-rende jamais les autres. Le composant ne lit que
`DraggingBlockContext` (change rarement — début/fin de drag) ; le sous-composant
interne `DraggingBlockBox` (monté **uniquement** pour la boîte activement
déplacée) est le seul à lire `DragOffsetContext` (change à chaque
`pointermove`) — c'est ce découplage à deux contextes qui garantit qu'un
déplacement de souris ne re-rend jamais les boîtes voisines.

Détail cosmétique notable : le curseur `grab`/`grabbing` natif du navigateur
peut s'afficher blanc sur fond clair (illisible) — remplacé par un curseur SVG
inline (main noire à contour blanc) via `handCursorDataUri()`.

## `dragContext.ts` — les deux contextes du drag

- `DraggingBlockContext` : l'id du bloc en cours de déplacement (`null`
  sinon). Change rarement.
- `DragOffsetContext` : le delta normalisé `(dx, dy)` courant. Change à
  **chaque** `pointermove`.

Séparés dans leur propre fichier (pas de composant) pour que `BlockOverlay.tsx`
reste 100% exports de composants — une exigence de Fast Refresh (Vite/React).

## `editingCellContext.ts` — les deux contextes de l'édition de cellule

- `EditingCellContext` : quelle cellule (`{ blockId, column }`) est en édition,
  ou `null`. Change seulement au double-clic / à la validation/l'annulation.
- `CellDraftContext` : la valeur en cours de frappe + les handlers. Change à
  **chaque** frappe.

**Pourquoi ce découplage en deux contextes est important** : sans lui, il
faudrait passer `editingColumn`/`cellDraft` en props jusqu'à la ligne de
tableau, ce qui forcerait sa mémoïsation (le `useMemo` du rendu KaTeX) à se
recalculer à chaque frappe — exactement le bug que ce découplage évite. Une
frappe dans une cellule ne doit jamais invalider le rendu d'une autre cellule
ni d'une autre ligne.

## `TableRow.tsx` — `TableDataRow` / `TableDataCell` / `CellEditor`

Le composant le plus subtil du projet en termes de performance de rendu.

- **`TableDataRow`** (`React.memo`, keyed sur `block.id`) construit ses `<td>`
  directement à partir de `getRowCellTexts()` (simple découpage de chaîne,
  voir [`05-utils.md`](05-utils.md)) — pas besoin de faire parser toute la
  ligne comme un mini-tableau GFM par `ReactMarkdown`.
- **`TableDataCell`** (`React.memo`) mémoïse **son propre** rendu Markdown/KaTeX,
  sur `cellText` (la valeur brute de cette cellule) **uniquement** — pas sur
  la ligne ni le bloc entier. Conséquence : valider l'édition d'une cellule
  change `cellText` pour **cette** cellule, mais les `cellText` des autres
  colonnes de la même ligne restent des chaînes identiques → leur `useMemo`
  interne ne recalcule rien, React ne touche pas leur DOM.
- `isEditingThisCell` vient de `EditingCellContext` (pas d'une prop figée par
  un memo parent) — ce qui permet à la cellule de réagir seule au double-clic
  sans invalider la mémoïsation des autres.
- La colonne "Confiance" (colonne 0, injectée côté backend) n'est **jamais**
  éditable.
- **Détail CSS notable** : le `<input>` de `CellEditor` utilise `size={1}` et
  `[font:inherit] h-full block` — sans ça, passer en édition élargirait toute
  la colonne (largeur intrinsèque par défaut d'un `<input>`, ~20 caractères,
  appliquée par le navigateur *avant* le CSS `width: 100%` dans un tableau en
  `table-layout: auto`) ou ferait rétrécir la ligne (un `<input>` est
  `display: inline-block` par défaut et se dimensionne sur la métrique de
  police, pas la hauteur réelle de la cellule — souvent plus grande à cause du
  rendu KaTeX d'une formule).

## `CorrectionModal.tsx`

Modale simple : un `<textarea>` obligatoire ("Quelle était l'erreur ?"), deux
boutons Ignorer/Valider (Valider désactivé tant que le champ est vide).

## `TranscriptExport.tsx`

Rendu Markdown/LaTeX en lecture seule, **hors-écran**, dont le HTML sérialisé
est envoyé au backend pour génération du PDF via WeasyPrint (voir
[`../backend/04-service-corrections-export.md`](../backend/04-service-corrections-export.md)).

**Template délibérément distinct** de `ContentPanel`/`TableRow` : pas de
contrôles d'édition, pas de mémoïsation par cellule (inutile hors
interaction), et surtout **pas de classes Tailwind** — le HTML sérialisé part
sans sa feuille de style ; seules les classes reconnues par le template CSS du
backend (`block`, `table-wrap`, sélecteurs bruts `h2`/`table`/`th`/`td`) ont un
effet une fois côté serveur. `pages` reçoit des blocs déjà nettoyés par
`sanitizeExportPages()` (marqueurs `==...==` retirés) — pas besoin ici du
pipeline de surbrillance utilisé par `ContentPanel`.

## Voir aussi

- [`03-hooks.md`](03-hooks.md) — la logique derrière chacun de ces composants.
- [`05-utils.md`](05-utils.md) — `tableMarkdown.ts`, `geometry.ts`, `markdownHighlight.ts`, `confidenceColors.ts`, utilisés ici.
- [`../backend/02-service-claude.md`](../backend/02-service-claude.md) — la convention "un bloc par ligne de tableau" côté backend, qui justifie toute cette architecture de rendu.
