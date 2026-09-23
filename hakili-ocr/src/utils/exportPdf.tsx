/**
 * utils/exportPdf.tsx
 * Génère et télécharge un PDF de la transcription. Le rendu React hors-écran
 * (`TranscriptExport`, cf. son propre commentaire) sert uniquement à produire
 * du HTML/KaTeX déjà peint, dont on sérialise le DOM (`.innerHTML`) pour
 * l'envoyer au backend : WeasyPrint (app/services/pdf_export_service.py,
 * ocr-math-api) le convertit en un vrai PDF vectoriel (texte sélectionnable,
 * pagination CSS @page, tableaux qui passent à la ligne au lieu d'être
 * tronqués), tout en noir, marqueurs `==...==` déjà retirés avant rendu par
 * `sanitizeExportPages`.
 */
import { createRoot } from 'react-dom/client';
import { TranscriptExport } from '../components/result/TranscriptExport';
import { fetchApiBlob } from '../services/apiClient';
import { sanitizeExportPages } from './exportSanitize';
import type { ExportPage } from '../types';

/**
 * Monte `TranscriptExport` hors-écran (`left: -9999px`, jamais visible), attend
 * que React/KaTeX aient peint le DOM, sérialise ce HTML et le poste au backend
 * (`POST /export/pdf`) pour génération du PDF final, puis déclenche le
 * téléchargement du blob renvoyé.
 */
export async function exportTranscriptionToPdf(pages: ExportPage[], filename: string): Promise<void> {
  const container = document.createElement('div');
  container.style.position = 'fixed';
  container.style.left = '-9999px';
  container.style.top = '0';
  document.body.appendChild(container);

  const root = createRoot(container);
  root.render(<TranscriptExport pages={sanitizeExportPages(pages)} />);

  // Laisse React committer le DOM et KaTeX peindre les formules avant de
  // sérialiser (le rendu se fait au commit, mais un seul rAF peut encore
  // précéder le paint réel).
  await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));

  let html: string;
  try {
    html = container.innerHTML;
  } finally {
    root.unmount();
    container.remove();
  }

  const blob = await fetchApiBlob('/export/pdf', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ html }),
  });

  const url = URL.createObjectURL(blob);
  try {
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
  } finally {
    URL.revokeObjectURL(url);
  }
}
