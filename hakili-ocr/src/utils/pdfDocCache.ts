/**
 * utils/pdfDocCache.ts
 * Relie un `File` PDF déjà parsé par pdf-lib au `PDFDocument` correspondant, pour
 * que `loadPdf` (`pdfChunking.ts`) puisse réutiliser un document que
 * `rotatePdfFile` (`fileTransform.ts`) vient tout juste de charger et muter pour
 * appliquer une rotation, au lieu de reparser les mêmes octets. Un `WeakMap`
 * plutôt qu'un cache manuel : la clé (le `File` produit par `rotatePdfFile`)
 * n'est jamais réutilisée après le flux d'envoi, donc rien à invalider
 * explicitement — l'entrée disparaît d'elle-même une fois le `File` hors de
 * portée.
 */
import type { PDFDocument } from 'pdf-lib';

const cache = new WeakMap<File, PDFDocument>();

export function cachePdfDoc(file: File, doc: PDFDocument): void {
  cache.set(file, doc);
}

export function takeCachedPdfDoc(file: File): PDFDocument | undefined {
  return cache.get(file);
}
