/**
 * hooks/useTranscribe.ts
 * Point d'entrée réseau pour lancer une transcription et en suivre l'état,
 * normalisant les deux flux backend (image synchrone vs PDF asynchrone par
 * job) derrière une seule forme de retour (`UseTranscriptionResult`) — voir
 * `useTranscription` plus bas. Inclut aussi un mode mock (`USE_MOCK`) pour
 * développer l'UI sans backend ni clé API Anthropic.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import type {
  ApiResponse,
  TranscriptionResult,
  PDFTranscriptionResult,
  TranscriptionPayload,
  PdfJobStartResponse,
  PdfJobStatusResponse,
  PdfChunkedStartRequest,
  PdfChunkAckResponse,
  FailedPage,
} from '../types';
import { fetchApi, sendKeepaliveRequest, TranscribeError } from '../services/apiClient';
import { loadPdf, splitLoadedPdfIntoChunks, PDF_CHUNK_PAGE_COUNT_THRESHOLD, PDF_CHUNK_SIZE_PAGES } from '../utils/pdfChunking';

export { TranscribeError };

const USE_MOCK = false // Mettre à true pour utiliser les données factices ci-dessous
const MOCK_DELAY_MS = 3000;
// Compromis entre latence d'affichage (délai moyen avant qu'une page déjà
// prête côté backend n'apparaisse à l'écran) et charge du serveur (chaque
// poll reste une simple lecture mémoire, sans appel Claude).
const PDF_POLL_INTERVAL_MS = 4000;

// === MOCK DATA (mode démo, sans backend) ===
const MOCK_RESULT: TranscriptionResult = {
  blocks: [
    {
      id: 1,
      label: 'Exercice 1 - Énoncé',
      markdown: '**Soit** $f$ la fonction définie sur $\\mathbb{R}$ et dont la courbe est donnée ci-dessous :',
      bbox: { x_min: 0.05, y_min: 0.05, x_max: 0.95, y_max: 0.12 },
      confidence: 96,
    },
    {
      id: 2,
      label: 'Exercice 1a',
      markdown: '**a)** Résoudre graphiquement $f(x) = 1$',
      bbox: { x_min: 0.05, y_min: 0.45, x_max: 0.55, y_max: 0.52 },
      confidence: 92,
    },
  ],
  final_warning: 'Transcription partiellement fiable. Vérifiez les blocs marqués en rouge (confidence < 70%).',
};

const MOCK_PDF_RESULT: PDFTranscriptionResult = {
  pages: [
    { page_number: 1, image_b64: '', media_type: 'image/png', width: 800, height: 1100, ocr: MOCK_RESULT },
    {
      page_number: 2,
      image_b64: '',
      media_type: 'image/png',
      width: 800,
      height: 1100,
      ocr: {
        blocks: [
          {
            id: 1,
            label: 'Exercice 4',
            markdown: '$$\\lim_{x \\to 0} \\frac{\\sin x}{x} = 1$$',
            bbox: { x_min: 0.1, y_min: 0.1, x_max: 0.5, y_max: 0.2 },
            confidence: 94,
          },
        ],
        final_warning: undefined,
      },
    },
  ],
  final_warning: undefined,
  failed_pages: [],
};

/** Simule une transcription (image ou PDF) avec un délai fixe, pour le mode `USE_MOCK`. */
function mockTranscribe(isPdf: boolean): Promise<TranscriptionPayload> {
  return new Promise((resolve) => {
    setTimeout(() => resolve(isPdf ? MOCK_PDF_RESULT : MOCK_RESULT), MOCK_DELAY_MS);
  });
}

// === Appels réels ===

/** POST /transcribe : transcription synchrone d'une image simple, résultat direct. */
async function transcribeImage(file: File): Promise<TranscriptionResult> {
  const formData = new FormData();
  formData.append('file', file);

  const data = await fetchApi<ApiResponse>('/transcribe', { method: 'POST', body: formData });
  if (!data.success) {
    throw new TranscribeError('Le backend a retourné success=false sans détail.', 200, false);
  }
  if (data.result.final_warning === null) data.result.final_warning = undefined;
  return data.result;
}

/** POST /transcribe/pdf/start : démarre le job côté backend et retourne immédiatement son `job_id`. */
async function startPdfJob(file: File): Promise<PdfJobStartResponse> {
  const formData = new FormData();
  formData.append('file', file);
  return fetchApi<PdfJobStartResponse>('/transcribe/pdf/start', { method: 'POST', body: formData });
}

/** GET /transcribe/pdf/status/{jobId} : un point de polling, appelé en boucle par `useQuery` ci-dessous. */
async function fetchPdfJobStatus(jobId: string): Promise<PdfJobStatusResponse> {
  return fetchApi<PdfJobStatusResponse>(`/transcribe/pdf/status/${jobId}`);
}

/**
 * POST /transcribe/pdf/{jobId}/cancel : demande l'arrêt d'un job PDF en cours (bouton
 * "Annuler" explicite) — idempotent côté backend, ne lève jamais si le job est déjà
 * terminé. Utilisé ici pour l'arrêt volontaire ; la fermeture de l'onglet passe par
 * `sendKeepaliveRequest` directement (voir l'effet `pagehide` plus bas), pas par cette
 * fonction, car `fetch` normal n'a aucune garantie de survivre à la fermeture de la page.
 */
async function cancelPdfJob(jobId: string): Promise<PdfJobStatusResponse> {
  return fetchApi<PdfJobStatusResponse>(`/transcribe/pdf/${jobId}/cancel`, { method: 'POST' });
}

/**
 * POST /transcribe/pdf/start-chunked : ouvre un job PDF alimenté par plusieurs morceaux
 * envoyés successivement (voir `uploadPdfChunk` et `utils/pdfChunking.ts`), au lieu d'un
 * fichier complet en un seul POST comme `startPdfJob` — utilisé pour les PDF au-dessus de
 * `PDF_CHUNK_PAGE_COUNT_THRESHOLD` pages, où attendre la fin de l'upload avant de commencer
 * le traitement gaspillerait du temps.
 */
async function startPdfJobChunked(pagesExpected: number): Promise<PdfJobStartResponse> {
  const body: PdfChunkedStartRequest = { pages_expected: pagesExpected };
  return fetchApi<PdfJobStartResponse>('/transcribe/pdf/start-chunked', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

/**
 * POST /transcribe/pdf/{jobId}/chunk : envoie un morceau (sous-PDF produit par
 * `splitLoadedPdfIntoChunks`) d'un job ouvert par `startPdfJobChunked`. Les morceaux doivent être
 * envoyés strictement l'un après l'autre (voir la boucle séquentielle dans `startPdfChunkedFlow`
 * ci-dessous) — le backend numérote lui-même les pages à réception, dans l'ordre d'arrivée.
 */
async function uploadPdfChunk(
  jobId: string,
  chunkBlob: Blob,
  isLastChunk: boolean,
  signal?: AbortSignal
): Promise<PdfChunkAckResponse> {
  const formData = new FormData();
  formData.append('file', chunkBlob, 'chunk.pdf');
  formData.append('is_last_chunk', String(isLastChunk));
  return fetchApi<PdfChunkAckResponse>(`/transcribe/pdf/${jobId}/chunk`, { method: 'POST', body: formData, signal });
}

/** Progression réelle page par page d'un job PDF, telle qu'exposée par `UseTranscriptionResult.progress`. */
export interface TranscriptionProgress {
  pagesDone: number;
  pagesTotal: number;
}

/** Forme de retour normalisée de `useTranscription`, identique que le fichier envoyé soit une image ou un PDF. */
export interface UseTranscriptionResult {
  start: (file: File) => void;
  isPending: boolean;
  isError: boolean;
  error: TranscribeError | null;
  /** Progression réelle (page par page), uniquement disponible pour un PDF multi-pages. */
  progress: TranscriptionProgress | null;
  data: TranscriptionPayload | null;
  /**
   * Non-null dès qu'une page a rencontré une erreur indépendante de son contenu (clé API
   * invalide, crédit épuisé...) — le job a cessé de lancer de nouvelles pages, mais peut
   * encore être `"processing"` le temps que les pages déjà en vol terminent. `null` pour
   * une image simple.
   */
  fatalError: string | null;
  /** Pages définitivement en échec (tentatives épuisées, troncature, rasterisation). Toujours `[]` pour une image simple. */
  failedPages: FailedPage[];
  /**
   * Le polling lui-même échoue actuellement (backend injoignable, réseau coupé) — DISTINCT
   * de `isError` : `isError` reste nécessaire tel quel pour `LoadingScreen` (qui doit
   * pouvoir sortir l'utilisateur d'une perte de connexion survenant avant la toute première
   * page, sinon `refetchInterval` s'arrête et le spinner reste bloqué indéfiniment sans
   * signal). Une fois sur l'écran résultat, `isConnectionIssue` permet d'afficher un
   * indicateur non-bloquant (transitoire, react-query retente automatiquement) plutôt que
   * le modal réservé aux échecs définitifs. Toujours `false` hors flux PDF.
   */
  isConnectionIssue: boolean;
  /**
   * Même logique que `fatalError`, mais pour un arrêt demandé plutôt que subi — posé par
   * `cancel()` (bouton "Annuler") ou par la fermeture de l'onglet (voir l'effet `pagehide`
   * ci-dessous). `null` pour une image simple.
   */
  cancelReason: string | null;
  /**
   * Demande l'arrêt du job PDF en cours (no-op si aucun job actif) — les pages déjà en
   * plein appel réseau au moment de la demande se terminent normalement, seules les
   * suivantes sont sautées (voir `is_fatal_anthropic_error`/`cancel_pdf_job` côté backend,
   * même mécanique). No-op pour une image simple (pas de job à annuler).
   */
  cancel: () => void;
}

/**
 * Point d'entrée unique pour lancer une transcription (image ou PDF) et suivre son état.
 * - Image : un seul appel, résultat direct.
 * - PDF : POST /pdf/start (retourne un job_id) puis polling de /pdf/status/{job_id}
 *   jusqu'à ce que le traitement (page par page côté backend) soit terminé.
 */
export function useTranscription(): UseTranscriptionResult {
  const [pdfJobId, setPdfJobId] = useState<string | null>(null);

  const imageMutation = useMutation<TranscriptionResult, TranscribeError, File>({
    mutationFn: (file) => (USE_MOCK ? (mockTranscribe(false) as Promise<TranscriptionResult>) : transcribeImage(file)),
  });

  const startPdfMutation = useMutation<PdfJobStartResponse, TranscribeError, File>({
    mutationFn: startPdfJob,
    onSuccess: (data) => setPdfJobId(data.job_id),
  });

  const statusQuery = useQuery<PdfJobStatusResponse, TranscribeError>({
    queryKey: ['pdf-job-status', pdfJobId],
    queryFn: () => fetchPdfJobStatus(pdfJobId as string),
    enabled: pdfJobId !== null && !USE_MOCK,
    refetchInterval: (query) => (query.state.data?.status === 'processing' ? PDF_POLL_INTERVAL_MS : false),
  });

  // Refs tenues à jour à chaque rendu (pas via useEffect — une simple affectation pendant
  // le rendu suffit pour des valeurs déjà disponibles) : le gestionnaire `pagehide`
  // ci-dessous est enregistré une seule fois (tableau de dépendances vide) mais doit lire
  // le `pdfJobId`/statut COURANT au moment où l'onglet se ferme, pas celui de sa création.
  const pdfJobIdRef = useRef<string | null>(null);
  pdfJobIdRef.current = pdfJobId;
  const jobStatusRef = useRef<PdfJobStatusResponse['status'] | null>(null);
  jobStatusRef.current = statusQuery.data?.status ?? null;

  // Annule côté backend le job en cours si l'onglet se ferme (ou se recharge — aucune
  // distinction fiable n'existe entre les deux depuis un gestionnaire `pagehide`) pendant
  // qu'il est encore "processing" : sans ça, un job survit à la fermeture de l'onglet et
  // continue de consommer l'API Anthropic alors que plus personne ne peut voir le résultat
  // ni le récupérer (aucun mécanisme de reprise de job après un rechargement aujourd'hui —
  // `pdfJobId` n'est jamais persisté). `sendKeepaliveRequest` (pas `cancelPdfJob`/`fetchApi`
  // normal) : seul `fetch(..., { keepalive: true })` a une chance raisonnable d'aboutir une
  // fois la page en train de se fermer. Best-effort assumé, pas une garantie absolue (crash
  // navigateur, perte réseau au même instant...) — un filet de sécurité plus robuste (ex.
  // purge d'un job non pollé depuis longtemps côté serveur) resterait à ajouter séparément
  // si ce best-effort s'avère insuffisant en pratique.
  useEffect(() => {
    const handlePageHide = () => {
      const jobId = pdfJobIdRef.current;
      if (jobId && jobStatusRef.current === 'processing') {
        sendKeepaliveRequest(`/transcribe/pdf/${jobId}/cancel`);
      }
    };
    window.addEventListener('pagehide', handlePageHide);
    return () => window.removeEventListener('pagehide', handlePageHide);
  }, []);

  // Contrôleur de la boucle d'envoi des morceaux (flux chunké) : annulé par `cancel` pour que le
  // frontend cesse immédiatement d'envoyer de nouveaux morceaux (et coupe celui en cours d'upload).
  const chunkAbortRef = useRef<AbortController | null>(null);

  const cancel = useCallback(() => {
    chunkAbortRef.current?.abort();
    const jobId = pdfJobIdRef.current;
    if (!jobId) return;
    cancelPdfJob(jobId)
      .then(() => statusQuery.refetch())
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Résultat réellement affichable à cet instant : le backend expose déjà, à chaque poll,
  // exactement les pages transcrites jusqu'ici — triées par page_number mais pas forcément
  // contiguës depuis la page 1, les pages étant traitées en parallèle et pouvant finir dans
  // n'importe quel ordre (voir claude_service.py côté backend). On l'affiche tel quel, sans
  // filtrage : chaque page affiche son propre numéro dès qu'elle est prête (voir
  // AppContext.tsx), donc plus besoin d'attendre que les pages précédentes arrivent pour
  // montrer celle-ci.
  const streamedResult = useMemo<PDFTranscriptionResult | null>(
    () => statusQuery.data?.result ?? null,
    [statusQuery.data]
  );

  const [mockPdfResult, setMockPdfResult] = useState<PDFTranscriptionResult | null>(null);

  // État du flux chunké (gros PDF) : startPdfMutation n'est pas utilisée dans ce cas (le job est
  // ouvert via startPdfJobChunked, pas startPdfJob), donc son isPending/error ne le couvre pas —
  // isChunkedStarting comble la fenêtre entre l'appel à start() et le premier `pdfJobId` connu
  // (comptage de pages + ouverture du job, avant tout envoi de morceau), chunkUploadError
  // capture un échec de l'ouverture du job ou de l'envoi d'un morceau.
  const [isChunkedStarting, setIsChunkedStarting] = useState(false);
  const [chunkUploadError, setChunkUploadError] = useState<TranscribeError | null>(null);

  /**
   * Décide entre le flux `/pdf/start` classique (petit PDF, fichier entier en un POST) et le
   * flux chunké (gros PDF, au-dessus de PDF_CHUNK_PAGE_COUNT_THRESHOLD pages) : découpe le
   * fichier via `splitLoadedPdfIntoChunks` et envoie chaque morceau l'un après l'autre — le morceau
   * N+1 n'est envoyé qu'une fois la réponse du morceau N reçue, jamais en parallèle. C'est ce qui
   * permet au traitement Claude du morceau N de continuer côté backend PENDANT que N+1 est
   * envoyé (upload et traitement se chevauchent), sans qu'aucun ordre n'ait besoin d'être
   * communiqué au serveur : dès que `pdfJobId` est posé, le polling existant (`statusQuery`)
   * prend le relais exactement comme pour le flux classique — `progress`/`data` reflètent déjà
   * la progression réelle pendant que la boucle d'envoi ci-dessous est encore en cours.
   */
  const startPdfChunkedFlow = useCallback(async (file: File) => {
    let pageCount: number;
    let doc: Awaited<ReturnType<typeof loadPdf>>['doc'];
    try {
      ({ pageCount, doc } = await loadPdf(file));
    } catch {
      // PDF illisible côté client (fichier corrompu ?) — laisse le backend faire sa propre
      // validation via le flux classique plutôt que d'échouer silencieusement ici.
      startPdfMutation.mutate(file);
      return;
    }

    if (pageCount <= PDF_CHUNK_PAGE_COUNT_THRESHOLD) {
      startPdfMutation.mutate(file);
      return;
    }

    chunkAbortRef.current?.abort();
    const controller = new AbortController();
    chunkAbortRef.current = controller;
    setIsChunkedStarting(true);
    try {
      const jobStart = await startPdfJobChunked(pageCount);
      setPdfJobId(jobStart.job_id);
      // Annulé pendant l'ouverture du job : le `cancel` n'avait pas encore de job_id à annuler.
      if (controller.signal.aborted) {
        cancelPdfJob(jobStart.job_id).catch(() => {});
        return;
      }

      // Réutilise `doc` (déjà chargé par loadPdf ci-dessus) au lieu de reparser `file` — un
      // PDF de plusieurs centaines de pages ne doit être parsé qu'une seule fois.
      const chunks = await splitLoadedPdfIntoChunks(doc, PDF_CHUNK_SIZE_PAGES);
      for (let i = 0; i < chunks.length; i++) {
        if (controller.signal.aborted) break;
        const isLastChunk = i === chunks.length - 1;
        await uploadPdfChunk(jobStart.job_id, chunks[i].blob, isLastChunk, controller.signal);
      }
    } catch (err) {
      // Annulation volontaire : ni erreur affichée, ni message — le backend est déjà en train de s'arrêter.
      if (controller.signal.aborted) return;
      setChunkUploadError(
        err instanceof TranscribeError
          ? err
          : new TranscribeError("Échec de l'envoi du PDF par morceaux.", 0, true)
      );
    } finally {
      setIsChunkedStarting(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const start = useCallback((file: File) => {
    setPdfJobId(null);
    setMockPdfResult(null);
    setChunkUploadError(null);
    chunkAbortRef.current?.abort();
    imageMutation.reset();
    startPdfMutation.reset();

    const isPdf = file.type === 'application/pdf';
    if (isPdf && USE_MOCK) {
      mockTranscribe(true).then((result) => setMockPdfResult(result as PDFTranscriptionResult));
      return;
    }
    if (isPdf) {
      void startPdfChunkedFlow(file);
    } else {
      imageMutation.mutate(file);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // `chunkUploadError` doit rester dans cette liste : si POST /pdf/start-chunked lui-même échoue
  // (avant que pdfJobId ne soit jamais posé), aucune des autres conditions n'est vraie et
  // l'erreur serait silencieusement perdue en retombant sur la branche image ci-dessous
  // (isPending: false, isError: false — spinner bloqué indéfiniment sans message).
  const isPdfFlow =
    pdfJobId !== null ||
    startPdfMutation.isPending ||
    mockPdfResult !== null ||
    isChunkedStarting ||
    chunkUploadError !== null;

  if (isPdfFlow) {
    if (USE_MOCK) {
      return {
        start,
        isPending: mockPdfResult === null,
        isError: false,
        error: null,
        progress: null,
        data: mockPdfResult,
        fatalError: null,
        failedPages: [],
        isConnectionIssue: false,
        cancelReason: null,
        cancel: () => {},
      };
    }

    const jobStatus = statusQuery.data;
    const jobFailed = jobStatus?.status === 'error';

    return {
      start,
      isPending:
        startPdfMutation.isPending ||
        // `isChunkedStarting` reste vrai pendant TOUT l'envoi des morceaux (la réponse au dernier
        // est retardée par la contre-pression backend, donc presque jusqu'à la fin du job) :
        // dès qu'une page est affichable, il ne doit plus compter comme "pending", sinon
        // App.tsx renvoie sur 'loading' juste après le SET_RESULT et l'écran résultat n'apparaît jamais.
        (isChunkedStarting && streamedResult === null) ||
        (pdfJobId !== null && streamedResult === null && (!jobStatus || jobStatus.status === 'processing')),
      isError: startPdfMutation.isError || jobFailed || statusQuery.isError || chunkUploadError !== null,
      error:
        startPdfMutation.error ??
        chunkUploadError ??
        (jobFailed
          ? new TranscribeError(jobStatus?.error || 'Erreur inconnue lors du traitement du PDF.', 500, true)
          : statusQuery.error ?? null),
      progress: jobStatus ? { pagesDone: jobStatus.pages_done, pagesTotal: jobStatus.pages_total } : null,
      data: streamedResult,
      fatalError: jobStatus?.fatal_error ?? null,
      failedPages: streamedResult?.failed_pages ?? [],
      isConnectionIssue: statusQuery.isError,
      cancelReason: jobStatus?.cancel_reason ?? null,
      cancel,
    };
  }

  return {
    start,
    isPending: imageMutation.isPending,
    isError: imageMutation.isError,
    error: imageMutation.error,
    progress: null,
    data: imageMutation.data ?? null,
    fatalError: null,
    failedPages: [],
    isConnectionIssue: false,
    cancelReason: null,
    cancel: () => {},
  };
}
