/**
 * utils/pdfChunking.ts
 * Découpe un PDF en sous-PDF (copie structurelle des pages, aucun rendu de pixel — pdf-lib
 * uniquement) pour l'upload par morceaux des gros documents : voir `hooks/useTranscribe.ts`
 * côté envoi, et côté backend `POST /transcribe/pdf/start-chunked` + `POST
 * /transcribe/pdf/{job_id}/chunk` (`ocr-math-api/app/routers/transcription.py`). Le seul
 * rasteriseur reste PyMuPDF côté serveur, exactement comme pour un PDF envoyé en un seul
 * morceau — ce module ne rend jamais un pixel, il ne fait que recopier des pages entre
 * documents PDF. Motif nouveau dans le projet (`PDFDocument.create()` + `copyPages`/`addPage`)
 * mais même librairie que `fileTransform.ts` (qui, lui, mute un seul document déjà chargé pour
 * la rotation plutôt que d'en reconstruire de nouveaux).
 */
import { PDFDocument } from 'pdf-lib';
import { takeCachedPdfDoc } from './pdfDocCache';

/**
 * Au-delà de ce nombre de pages, le PDF est envoyé par morceaux plutôt qu'en un seul POST —
 * voir le choix de flux dans `useTranscribe.ts`. Sous ce seuil, découper n'apporterait rien
 * (juste des allers-retours réseau en plus) : le flux `/pdf/start` classique reste inchangé.
 * Abaissé de 30 à 10 (2026-09-21) : le flux `/pdf/start` rasterise *toutes* les pages avant
 * même de créer le job (voir `start_pdf_transcription`, `transcription.py`) — pour un PDF de
 * taille moyenne, ça retardait le tout premier appel Claude (donc la première page affichée)
 * de la durée totale de rasterisation du document. Le flux chunké rasterise en arrière-plan,
 * morceau par morceau, donc la première page apparaît plus tôt dès qu'on y bascule plus tôt.
 */
export const PDF_CHUNK_PAGE_COUNT_THRESHOLD = 10;

/**
 * Nombre de pages par morceau — nettement au-dessus d'`ANTHROPIC_CONCURRENCY` pour qu'un
 * morceau sature le sémaphore de traitement pendant que le suivant est envoyé, sans être si
 * gros que la rasterisation d'un morceau devienne elle-même un goulot d'étranglement notable.
 *
 * Historique production : abaissé de 20 à 10 (2026-09-21), en cohérence avec la baisse
 * d'`ANTHROPIC_CONCURRENCY` (6 → 2) suite à l'incident OOM — voir
 * docs/decisions-et-limites-connues.md.
 *
 * Remonté à 12 le même jour (ce commit) — valeur de TEST LOCAL uniquement, en cohérence avec
 * le retour temporaire d'`ANTHROPIC_CONCURRENCY` à 6 côté backend pour exercer le nouveau
 * PrioritySemaphore (voir ocr-math-api/app/services/claude_service.py). Le serveur de
 * production reste sur la paire (concurrence 2, morceaux de 10) tant que le swap n'est pas
 * en place — ne pas redéployer 12 avant confirmation.
 */
export const PDF_CHUNK_SIZE_PAGES = 12;

/**
 * Un PDF chargé une seule fois (`PDFDocument.load`, qui analyse toute la structure du
 * document — table des objets, pages, ressources) et son nombre de pages, prêt à être réutilisé
 * par `splitLoadedPdfIntoChunks` sans reparser le fichier. Charger un PDF de plusieurs centaines
 * de pages est un vrai coût CPU (thread principal, pdf-lib n'a pas de mode Worker) : le séparer
 * en deux étapes (charger, puis découper) permet à l'appelant de connaître le nombre de pages
 * — nécessaire pour décider s'il découpe ou non, voir `PDF_CHUNK_PAGE_COUNT_THRESHOLD` — sans
 * jamais payer ce coût deux fois pour le même fichier.
 */
export interface LoadedPdf {
  pageCount: number;
  doc: PDFDocument;
}

/**
 * Charge `file` et rapporte son nombre de pages — voir `LoadedPdf` pour pourquoi ce chargement
 * est réutilisable. Si `file` est le fichier pivoté produit par `rotatePdfFile`
 * (`fileTransform.ts`), le `PDFDocument` a déjà été chargé et muté pour appliquer la rotation :
 * `pdfDocCache` permet de le réutiliser tel quel plutôt que de reparser les mêmes octets une
 * seconde fois (coût réel sur un document de plusieurs centaines de pages).
 */
export async function loadPdf(file: File): Promise<LoadedPdf> {
  const cachedDoc = takeCachedPdfDoc(file);
  if (cachedDoc) {
    return { pageCount: cachedDoc.getPageCount(), doc: cachedDoc };
  }

  const bytes = await file.arrayBuffer();
  const doc = await PDFDocument.load(bytes);
  return { pageCount: doc.getPageCount(), doc };
}

export interface PdfChunk {
  /** Position (0-based) de la première page de ce morceau dans le document d'origine. */
  startPageIndex: number;
  /** Nombre de pages contenues dans ce morceau. */
  pageCount: number;
  /** Le sous-PDF lui-même, prêt à être envoyé tel quel à `POST /pdf/{job_id}/chunk`. */
  blob: Blob;
}

/**
 * Découpe un PDF déjà chargé (`loadPdf`) en une suite de sous-PDF de `pagesPerChunk` pages
 * maximum chacun — copie structurelle des pages (`copyPages`/`addPage`), sans rendu de pixel, et
 * sans reparser `file` : `doc` est réutilisé tel quel. Les morceaux sont produits dans l'ordre du
 * document ; à charge de l'appelant de les envoyer dans cet ordre et un par un (le backend
 * numérote les pages lui-même à réception, dans l'ordre d'arrivée — voir la note sur l'envoi
 * strictement séquentiel dans `useTranscribe.ts` : aucun numéro d'ordre n'est transmis,
 * `startPageIndex` ici ne sert qu'à un éventuel affichage de progression d'envoi côté client, pas
 * à la numérotation finale des pages).
 */
export async function splitLoadedPdfIntoChunks(doc: PDFDocument, pagesPerChunk: number): Promise<PdfChunk[]> {
  const totalPages = doc.getPageCount();

  const chunks: PdfChunk[] = [];
  for (let startPageIndex = 0; startPageIndex < totalPages; startPageIndex += pagesPerChunk) {
    const pageCount = Math.min(pagesPerChunk, totalPages - startPageIndex);
    const pageIndices = Array.from({ length: pageCount }, (_, i) => startPageIndex + i);

    const chunkDoc = await PDFDocument.create();
    const copiedPages = await chunkDoc.copyPages(doc, pageIndices);
    copiedPages.forEach((page) => chunkDoc.addPage(page));

    const chunkBytes = await chunkDoc.save();
    chunks.push({
      startPageIndex,
      pageCount,
      blob: new Blob([chunkBytes as BlobPart], { type: 'application/pdf' }),
    });
  }

  return chunks;
}
