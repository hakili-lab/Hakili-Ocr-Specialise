/**
 * types/index.ts
 * Types partagés de l'application : d'un côté le miroir TypeScript des schémas
 * Pydantic exposés par le backend (`ocr-math-api`), de l'autre l'état/les
 * actions du reducer applicatif (`context/AppContext.tsx`). Pas de génération
 * automatique entre les deux projets — ces types doivent être resynchronisés
 * manuellement quand un schéma backend change.
 */

// === Types liés au backend ===

/** Un bloc de texte transcrit par l'OCR (un paragraphe simple, ou une ligne de tableau). */
export interface TranscriptionBlock {
  id: number;
  label: string;
  /** Markdown/LaTeX brut renvoyé par Claude (ou édité par l'utilisateur). */
  markdown: string;
  /** Position normalisée [0,1] sur l'image *redimensionnée* envoyée à Claude. */
  bbox: BoundingBox;
  /** Score de confiance 0-100 attribué par le backend. */
  confidence: number;
}

/** Boîte englobante normalisée en fractions [0,1] de l'image affichée. */
export interface BoundingBox {
  x_min: number;
  y_min: number;
  x_max: number;
  y_max: number;
}

/** Réponse de transcription pour une image simple (un seul jeu de blocs). */
export interface TranscriptionResult {
  blocks: TranscriptionBlock[];
  /** Optionnel — le backend peut envoyer `null` ou omettre le champ. */
  final_warning?: string;
}

/** Enveloppe de réponse telle que renvoyée par `POST /transcribe`. */
export interface ApiResponse {
  success: boolean;
  result: TranscriptionResult;
}

// === Types de l'application ===

/** Les 4 écrans possibles, pilotés par `AppContext` (pas de router). */
export type Screen = 'upload' | 'preview' | 'loading' | 'result';

/** État global de l'application (un seul `useReducer`, cf. `context/AppContext.tsx`). */
export interface AppState {
  currentScreen: Screen;
  uploadedImage: File | null;
  imagePreviewUrl: string | null;
  transcriptionResult: TranscriptionResult | null;
  selectedBlockId: number | null;
  /** Non-null uniquement quand le document chargé est un PDF multi-pages. */
  pdfResult: PDFTranscriptionResult | null;
  /**
   * Numéro de la page actuellement affichée, moins 1 (0-based) — PAS un index dans
   * `pdfResult.pages` : les pages peuvent être transcrites (et donc arriver) dans
   * n'importe quel ordre, donc `pdfResult.pages` n'est pas garanti trié par position
   * d'arrivée. Pour retrouver la page effectivement affichée, chercher dans
   * `pdfResult.pages` celle dont `page_number === currentPageIndex + 1` (voir
   * `findPageByNumber` dans `context/AppContext.tsx`) plutôt que d'indexer directement.
   */
  currentPageIndex: number;
  /**
   * Nombre total de pages du document PDF, connu dès le premier statut de job (voir
   * `useTranscribe.ts`), indépendamment du nombre de pages déjà transcrites dans
   * `pdfResult.pages` tant que le job est encore en cours (voir `MERGE_PDF_RESULT`).
   * `null` pour une image simple.
   */
  pdfPagesTotal: number | null;
}

/**
 * Actions du reducer applicatif. `UPDATE_BLOCK_MARKDOWN`/`UPDATE_BLOCK_BBOX`
 * mettent à jour un seul bloc en place (cf. `AppContext.tsx`), sans jamais
 * reparser tout le résultat de transcription.
 */
export type AppAction =
  | { type: 'NAVIGATE'; screen: Screen }
  | { type: 'SET_IMAGE'; file: File; previewUrl: string }
  | { type: 'CONFIRM_UPLOAD'; file: File; previewUrl?: string }
  | { type: 'CANCEL_PREVIEW' }
  | { type: 'SET_RESULT'; result: TranscriptionResult; pagesTotal?: number | null }
  /**
   * Ajoute les pages nouvellement prêtes à un `pdfResult` déjà affiché (écran 'result'
   * déjà atteint) sans toucher aux pages déjà chargées/éditées — `result.pages` est
   * toujours une extension (plus de pages, jamais moins) du contenu déjà stocké, jamais
   * un remplacement de son contenu, mais pas forcément dans l'ordre d'arrivée précédent :
   * les pages peuvent finir dans n'importe quel ordre. Si la page actuellement affichée
   * (`currentPageIndex`) vient tout juste de devenir prête, `AppContext.tsx` rafraîchit
   * aussi `transcriptionResult`/`imagePreviewUrl` pour elle.
   */
  | { type: 'MERGE_PDF_RESULT'; result: PDFTranscriptionResult; pagesTotal?: number | null }
  /** Navigue vers le NUMÉRO de page donné (1-based) — pas un index dans `pdfResult.pages`. */
  | { type: 'SET_PAGE'; pageNumber: number }
  | { type: 'SELECT_BLOCK'; blockId: number | null }
  | { type: 'UPDATE_BLOCK_MARKDOWN'; blockId: number; markdown: string }
  | { type: 'UPDATE_BLOCK_BBOX'; blockId: number; bbox: BoundingBox }
  | { type: 'RESET' };

// === Types PDF multi-pages ===

/** Une page rasterisée par le backend (PyMuPDF, 150 DPI) + son propre résultat OCR. */
export interface PageResult {
  page_number: number;
  /** Image de la page encodée en base64 (pas d'URL — servie inline par l'API). */
  image_b64: string;
  media_type: string;
  width: number;
  height: number;
  ocr: TranscriptionResult;
}

/** Réponse complète une fois toutes les pages d'un PDF transcrites. */
export interface PDFTranscriptionResult {
  pages: PageResult[];
  final_warning?: string;
}

/** Union des deux formes de payload que peut renvoyer une transcription (image seule ou PDF). */
export type TranscriptionPayload = TranscriptionResult | PDFTranscriptionResult;

/**
 * Un jeu de blocs à exporter (PDF/Excel, `utils/exportPdf.tsx`/`utils/exportExcel.ts`)
 * avec le numéro de la page source dont ils proviennent — une seule entrée avec
 * `pageNumber: 1` pour une image simple, une entrée par page (son `page_number`
 * PyMuPDF réel) pour un PDF.
 */
export interface ExportPage {
  pageNumber: number;
  blocks: TranscriptionBlock[];
}

// === Types job PDF (traitement en arrière-plan + polling) ===

/** Réponse immédiate de `POST /transcribe/pdf/start` — le traitement se poursuit en tâche de fond côté backend. */
export interface PdfJobStartResponse {
  job_id: string;
  pages_total: number;
}

export type PdfJobStatus = 'processing' | 'done' | 'error';

/** Réponse de `GET /transcribe/pdf/status/{job_id}`, interrogée en polling par `useTranscribe.ts`. */
export interface PdfJobStatusResponse {
  job_id: string;
  status: PdfJobStatus;
  pages_done: number;
  pages_total: number;
  /**
   * Peut être présent AVANT que `status` passe à `'done'` : dès qu'au moins une page est
   * transcrite, le backend expose un résultat partiel (les pages prêtes jusqu'ici,
   * triées par `page_number`, potentiellement trouées si des pages sont encore en cours
   * ou ont échoué). `status` reste la seule source de vérité pour savoir si le document
   * entier est terminé.
   */
  result?: PDFTranscriptionResult;
  /** Présent uniquement quand `status === 'error'`. */
  error?: string;
}

// === Types upload PDF par morceaux (gros documents, voir utils/pdfChunking.ts) ===

/** Corps de `POST /transcribe/pdf/start-chunked` — ouvre un job alimenté par plusieurs morceaux au lieu d'un fichier complet. */
export interface PdfChunkedStartRequest {
  pages_expected: number;
}

/** Réponse de `POST /transcribe/pdf/{job_id}/chunk` — un accusé de réception, pas le résultat final (voir `PdfJobStatusResponse` pour ça). */
export interface PdfChunkAckResponse {
  job_id: string;
  pages_received: number;
  status: PdfJobStatus;
}
