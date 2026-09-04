# Les 4 écrans de l'application

> Dernière vérification : commit `4a30eae`. Code : `hakili-ocr/src/components/{UploadScreen,PreviewScreen,LoadingScreen,ResultScreen}.tsx`.

Cette page décrit **ce que voit l'utilisateur** à chaque étape, avec le
composant et les actions techniques correspondantes — de quoi se faire une
idée de l'interface sans avoir à lancer l'application.

## Écran 1/4 — Dépôt du fichier (`UploadScreen.tsx`)

**Ce que voit l'utilisateur** : un titre ("Déposez une page. Vous récupérez un
texte propre.") au-dessus d'une grande zone de dépôt avec une icône de
document. On peut soit glisser-déposer un fichier, soit cliquer sur "Choisir
un fichier". Trois étiquettes flottantes en dessous ("MANUSCRIT", "TABLEAUX",
"FORMULES") illustrent le type de contenu pris en charge. Une erreur de type
ou de taille de fichier s'affiche directement dans la zone, sans popup.

> Note : l'étiquette "FORMULES" est un reliquat visuel de l'ancien
> positionnement du produit (mathématiques) — voir
> [`../decisions-et-limites-connues.md`](../decisions-et-limites-connues.md).

**Ce qui se passe techniquement** : `UploadZone` (sous-composant) valide côté
client le type (PNG/JPEG/PDF) et la taille (5 Mo pour une image, 500 Mo pour
un PDF — miroir des limites backend, voir
[`../backend/07-configuration.md`](../backend/07-configuration.md)) **avant**
de dispatcher `SET_IMAGE`. Le nombre de pages d'un PDF n'est vérifié que côté
serveur, une fois le fichier réellement envoyé.

## Écran 2/4 — Aperçu et rotation (`PreviewScreen.tsx`)

**Ce que voit l'utilisateur** : le document affiché en grand, avec un bouton
de rotation à 90° et, pour un PDF multi-pages, une navigation "page précédente
/ page suivante" ainsi qu'un sélecteur "Cette page" / "Toutes les pages" pour
choisir la portée de la rotation. Un bouton "Transcrire le document" valide et
lance l'envoi.

**Pourquoi cet écran existe** : beaucoup de photos/scans sont mal orientés
(90°, 180°). Corriger la rotation **avant** l'envoi évite une transcription
inutile sur un document à l'envers, et garantit que les boîtes affichées
ensuite (écran Résultat) restent dans le bon sens.

**Ce qui se passe techniquement** :
- Pour un PDF, l'aperçu page par page est produit **côté client** par
  `usePdfPreview()` (rendu `pdfjs-dist` dans un Web Worker — voir
  [`05-utils.md`](05-utils.md)) : ce rendu ne sert **qu'à l'aperçu**, il n'a
  aucune influence sur le fichier réellement envoyé.
- La rotation choisie n'est appliquée **au fichier réel** qu'au moment de
  cliquer sur "Transcrire" (`applyRotation()`, `fileTransform.ts`) — avant ça,
  elle n'est qu'un `transform: rotate(...)` CSS sur l'aperçu. Pour une image,
  le fichier est redessiné sur un `<canvas>` ; pour un PDF, la rotation est
  écrite dans la métadonnée `/Rotate` de chaque page (respectée par PyMuPDF
  côté serveur), sans re-rasteriser le contenu.
- Au clic sur "Transcrire", `CONFIRM_UPLOAD` est dispatché → écran de
  chargement.

## Écran 3/4 — Chargement (`LoadingScreen.tsx`)

**Ce que voit l'utilisateur** : un anneau de progression tournant en boucle
(spinner indéterminé, pas un pourcentage réel), un texte de statut qui change
toutes les 2,5 secondes ("Lecture des écritures…", "Repérage des tableaux…",
"Vérification des formules…", "Mise en forme du texte…" — purement
illustratif, pas un vrai suivi backend), et une barre qui balaie de gauche à
droite. En cas d'échec, l'écran affiche un message d'erreur et un bouton
"Réessayer" qui ramène à l'écran de dépôt.

> Note : "Vérification des formules…" est, comme "FORMULES" sur l'écran
> précédent, un texte hérité de l'ancien positionnement mathématique du
> produit.

**Ce qui se passe techniquement** : cet écran est **volontairement
générique**, pour une image comme pour un PDF — aucun compteur "Page X / Y"
ici, même si le backend expose une vraie progression pour un PDF
(`progress.pagesDone/pagesTotal`, voir [`03-hooks.md`](03-hooks.md)). Dès que
la **première page** est prête, l'app bascule automatiquement vers l'écran
Résultat (`SET_RESULT`, déclenché dans `App.tsx`) — c'est cet écran-là qui
prend le relais pour afficher la progression réelle des pages suivantes (petit
indicateur "Transcription en cours…" dans `ResultHeader`).

## Écran 4/4 — Résultat (`ResultScreen.tsx` + `components/result/*`)

**Ce que voit l'utilisateur** : un écran en deux colonnes.

- **Colonne de gauche** ("Copie annotée") : l'image du document avec des
  boîtes colorées superposées — vert = fiable, orange = à vérifier, rouge =
  incertain (légende affichée en haut de la colonne). Survoler une boîte
  affiche son pourcentage de confiance. Une boîte peut être **glissée** à la
  souris pour corriger sa position si elle est mal placée.
- **Colonne de droite** ("Contenu extrait") : le texte transcrit, en Markdown
  rendu (gras, formules, tableaux reconstitués). Cliquer sur un bloc le met en
  évidence à gauche ; double-cliquer permet de corriger son texte en place
  (un bloc simple s'édite en entier, une cellule de tableau s'édite seule).
  Un contenu que Claude n'était pas sûr d'avoir bien lu apparaît surligné en
  jaune pâle.
- **En haut** (`ResultHeader`) : logo, navigation entre pages pour un PDF (avec
  l'indicateur "Transcription en cours…" pendant qu'un document PDF continue
  d'être traité en arrière-plan), et deux boutons "Exporter en Excel"/
  "Exporter en PDF" (désactivés tant que le document n'est pas entièrement
  transcrit).
- **En bas** : un bouton "Nouvelle image" qui réinitialise tout.
- Après avoir validé une correction de texte, une **petite fenêtre modale**
  demande "Quelle était l'erreur dans la transcription d'origine ?" — un champ
  obligatoire, utilisé pour construire un jeu de données de corrections (voir
  [`../backend/04-service-corrections-export.md`](../backend/04-service-corrections-export.md)).

**Ce qui se passe techniquement** : cet écran est un pur orchestrateur — toute
la logique (édition, sélection, drag, capture de corrections) vit dans des
hooks dédiés (voir [`03-hooks.md`](03-hooks.md)), et le rendu est découpé en
sous-composants (voir [`04-composants-result.md`](04-composants-result.md)).

## Voir aussi

- [`01-etat-global.md`](01-etat-global.md) — les actions dispatchées par chaque écran.
- [`03-hooks.md`](03-hooks.md) — toute la logique d'édition et de réseau derrière l'écran Résultat.
- [`../architecture/01-vue-ensemble.md`](../architecture/01-vue-ensemble.md) — le diagramme de flux global.
