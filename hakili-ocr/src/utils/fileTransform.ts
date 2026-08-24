/**
 * fileTransform.ts
 * Applique une rotation réelle au fichier (pas juste à l'aperçu) avant l'envoi au backend :
 * - image (PNG/JPEG) : redessinée pivotée sur un <canvas>
 * - PDF : rotation écrite dans la métadonnée /Rotate de chaque page (respectée par PyMuPDF
 *   côté backend), sans re-rasteriser le contenu
 */
import { PDFDocument, degrees } from 'pdf-lib';
import { cachePdfDoc } from './pdfDocCache';

export type RotationAngle = 0 | 90 | 180 | 270;

/** Redessine l'image pivotée sur un `<canvas>` hors-écran ; `angle` 90/270 permute largeur et hauteur du canvas. */
export async function rotateImageFile(file: File, angle: RotationAngle): Promise<File> {
  if (angle === 0) return file;

  const bitmap = await createImageBitmap(file);
  const swapDimensions = angle === 90 || angle === 270;
  const canvas = document.createElement('canvas');
  canvas.width = swapDimensions ? bitmap.height : bitmap.width;
  canvas.height = swapDimensions ? bitmap.width : bitmap.height;

  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error("Impossible d'obtenir le contexte 2D du canvas.");

  ctx.translate(canvas.width / 2, canvas.height / 2);
  ctx.rotate((angle * Math.PI) / 180);
  ctx.drawImage(bitmap, -bitmap.width / 2, -bitmap.height / 2);

  const mimeType = file.type || 'image/png';
  const blob = await new Promise<Blob | null>((resolve) =>
    canvas.toBlob(resolve, mimeType, 0.95)
  );
  if (!blob) throw new Error("Échec de la génération de l'image pivotée.");

  return new File([blob], file.name, { type: mimeType });
}

/** Écrit la rotation par page dans la métadonnée PDF (`/Rotate`) — additionnée à une rotation déjà présente sur la page, pas remplacée. */
export async function rotatePdfFile(
  file: File,
  rotations: Record<number, RotationAngle>
): Promise<File> {
  const hasRotation = Object.values(rotations).some((angle) => angle !== 0);
  if (!hasRotation) return file;

  const bytes = await file.arrayBuffer();
  const pdfDoc = await PDFDocument.load(bytes);
  const pages = pdfDoc.getPages();

  for (const [indexStr, angle] of Object.entries(rotations)) {
    if (angle === 0) continue;
    const page = pages[Number(indexStr)];
    if (!page) continue;
    const currentAngle = page.getRotation().angle;
    page.setRotation(degrees((currentAngle + angle) % 360));
  }

  const rotatedBytes = await pdfDoc.save();
  const rotatedFile = new File([rotatedBytes as BlobPart], file.name, { type: 'application/pdf' });
  // `pdfDoc` reste valide après `.save()` (qui ne fait que sérialiser un instantané) — le
  // mettre en cache ici évite à `loadPdf` (pdfChunking.ts) de reparser ces mêmes octets
  // juste après, quand ce fichier pivoté poursuit vers le flux d'envoi/chunking.
  cachePdfDoc(rotatedFile, pdfDoc);
  return rotatedFile;
}

/** Point d'entrée unique appelé par `PreviewScreen` — délègue selon `isPdf` vers l'une des deux stratégies ci-dessus. */
export async function applyRotation(
  file: File,
  rotation: RotationAngle | Record<number, RotationAngle>,
  isPdf: boolean
): Promise<File> {
  return isPdf
    ? rotatePdfFile(file, rotation as Record<number, RotationAngle>)
    : rotateImageFile(file, rotation as RotationAngle);
}
