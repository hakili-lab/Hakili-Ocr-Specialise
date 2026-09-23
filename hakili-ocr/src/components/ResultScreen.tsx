/**
 * components/ResultScreen.tsx
 * Écran 4/4 : orchestration pure. Instancie les hooks d'état (édition de bloc,
 * édition de cellule, sélection de ligne, capture de correction, drag de bbox)
 * et compose les panneaux de rendu (`ResultHeader`, `AnnotatedImagePanel`,
 * `ContentPanel`, `CorrectionModal`) — voir `hooks/use*` et
 * `components/result/*` pour le détail de chaque sous-système. Ne garde en
 * propre que ce qui est partagé entre panneaux : `blockRefs`/`blocksRef`
 * (refs miroir, cf. commentaires ci-dessous), `imageContainerRef` et les deux
 * providers de contexte (`EditingCellContext`/`CellDraftContext`).
 */
import { useRef, useCallback, useEffect, useState } from 'react';
import { useApp } from '../context/AppContext';
import type { BoundingBox, TranscriptionBlock } from '../types';
import type { TranscriptionProgress } from '../hooks/useTranscribe';
import { EditingCellContext, CellDraftContext } from './result/editingCellContext';
import { useBlockDrag } from '../hooks/useBlockDrag';
import { useBlockEditing } from '../hooks/useBlockEditing';
import { useCellEditing } from '../hooks/useCellEditing';
import { useRowSelection } from '../hooks/useRowSelection';
import { useCorrectionCapture } from '../hooks/useCorrectionCapture';
import { ResultHeader } from './result/ResultHeader';
import { AnnotatedImagePanel } from './result/AnnotatedImagePanel';
import { ContentPanel } from './result/ContentPanel';
import { CorrectionModal } from './result/CorrectionModal';
import { exportTranscriptionToPdf } from '../utils/exportPdf';
import { exportTranscriptionToExcel } from '../utils/exportExcel';

type ResultScreenProps = {
  /** Progression réelle page par page du job PDF en arrière-plan — `null` pour une image simple ou une fois le hook démonté de tout job. */
  progress?: TranscriptionProgress | null;
};

export default function ResultScreen({ progress = null }: ResultScreenProps) {
  const { state, dispatch } = useApp();
  const { transcriptionResult, imagePreviewUrl, selectedBlockId, pdfResult, currentPageIndex, pdfPagesTotal, uploadedImage } = state;
  const [isExportingPdf, setIsExportingPdf] = useState(false);
  const [isExportingExcel, setIsExportingExcel] = useState(false);

  const blockRefs = useRef<Map<number, HTMLElement>>(new Map());
  const blocksRef = useRef<TranscriptionBlock[]>([]);
  const imageContainerRef = useRef<HTMLDivElement>(null);

  const blocks = transcriptionResult?.blocks ?? [];
  blocksRef.current = blocks;

  const scrollToBlock = useCallback(
    (blockId: number) => dispatch({ type: 'SELECT_BLOCK', blockId }),
    [dispatch]
  );

  const { pendingCorrection, isSubmittingCorrection, captureCorrection, handleCancelCorrection, handleSubmitCorrection } =
    useCorrectionCapture({ imagePreviewUrl });

  const { editingBlockId, editDraft, setEditDraft, handleStartEditBlock, handleCancelEditBlock, handleConfirmEditBlock } =
    useBlockEditing({ blocks, dispatch, onConfirm: captureCorrection });

  const { editingCell, cellDraftContextValue, handleStartEditCell } =
    useCellEditing({ blocksRef, dispatch, onConfirm: captureCorrection });

  const { handleRowClick, cancelPendingRowClick } = useRowSelection({ dispatch });

  // Un double-clic sur une cellule annule le clic de sélection de ligne en
  // attente (cf. useRowSelection) avant de démarrer l'édition de cette cellule.
  const handleCellDoubleClick = useCallback(
    (blockId: number, column: number) => {
      cancelPendingRowClick();
      handleStartEditCell(blockId, column);
    },
    [cancelPendingRowClick, handleStartEditCell]
  );

  const getBBox = useCallback(
    (blockId: number) => blocksRef.current.find((b) => b.id === blockId)?.bbox,
    []
  );
  const handleBlockDragEnd = useCallback(
    (blockId: number, bbox: BoundingBox) => dispatch({ type: 'UPDATE_BLOCK_BBOX', blockId, bbox }),
    [dispatch]
  );
  // Un clic sans franchir le seuil de drag garde le comportement de sélection existant.
  const { draggingBlockId, dragOffset, getBlockPointerHandlers } = useBlockDrag({
    containerRef: imageContainerRef,
    getBBox,
    onDragEnd: handleBlockDragEnd,
    onClick: scrollToBlock,
  });

  // Scroll vers le bloc sélectionné
  useEffect(() => {
    if (selectedBlockId !== null) {
      const el = blockRefs.current.get(selectedBlockId);
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }, [selectedBlockId]);

  const isPdf = pdfResult !== null;
  // Nombre total de pages du document (affichage du header ET borne de navigation) —
  // l'utilisateur peut naviguer vers n'importe quelle page jusqu'à `totalPages`, y
  // compris une page pas encore transcrite (affichée en placeholder ci-dessous), puisque
  // les pages peuvent finir dans n'importe quel ordre.
  const totalPages = pdfResult ? (pdfPagesTotal ?? pdfResult.pages.length) : 1;
  const isStreaming = isPdf && progress !== null && progress.pagesDone < progress.pagesTotal;
  // `currentPageIndex` EST le numéro de page moins 1 par construction (voir AppContext.tsx),
  // jamais un index dans `pdfResult.pages` — pas besoin de le retrouver via une recherche.
  const pageNumber = currentPageIndex + 1;

  const handleNewImage = () => {
    dispatch({ type: 'RESET' });
  };

  /**
   * Un jeu de blocs par page source, chacun avec son propre numéro de page (colonne "Page"
   * des exports) — une seule entrée pour une image simple. Trié explicitement par numéro de
   * page : le backend renvoie déjà `pdfResult.pages` dans cet ordre (_build_pdf_result), mais
   * le tri ici est un filet de sécurité peu coûteux et indépendant de l'ordre de traitement,
   * pour garantir que l'export reste dans l'ordre des pages quel que soit celui d'arrivée.
   */
  const buildExportPages = (): { pageNumber: number; blocks: TranscriptionBlock[] }[] =>
    pdfResult
      ? [...pdfResult.pages]
          .sort((a, b) => a.page_number - b.page_number)
          .map((page) => ({ pageNumber: page.page_number, blocks: page.ocr.blocks }))
      : [{ pageNumber: 1, blocks }];

  const handleExportPdf = async () => {
    if (isExportingPdf) return;
    setIsExportingPdf(true);
    try {
      const baseName = uploadedImage?.name.replace(/\.[^./\\]+$/, '') || 'transcription';
      await exportTranscriptionToPdf(buildExportPages(), `${baseName}.pdf`);
    } finally {
      setIsExportingPdf(false);
    }
  };

  const handleExportExcel = async () => {
    if (isExportingExcel) return;
    setIsExportingExcel(true);
    try {
      const baseName = uploadedImage?.name.replace(/\.[^./\\]+$/, '') || 'transcription';
      await exportTranscriptionToExcel(buildExportPages(), `${baseName}.xlsx`);
    } finally {
      setIsExportingExcel(false);
    }
  };

  const handlePrevPage = () => {
    if (currentPageIndex > 0) {
      dispatch({ type: 'SET_PAGE', pageNumber: currentPageIndex });
    }
  };

  const handleNextPage = () => {
    if (currentPageIndex < totalPages - 1) {
      dispatch({ type: 'SET_PAGE', pageNumber: currentPageIndex + 2 });
    }
  };

  if (!transcriptionResult) {
    // Page ciblée pas encore transcrite (navigation vers un numéro pas encore prêt) —
    // reste sur l'écran avec le header actif (navigation/export toujours utilisables)
    // plutôt que de rendre un écran vide ; se résout tout seul dès que la page arrive
    // (voir MERGE_PDF_RESULT dans AppContext.tsx).
    if (!isPdf) return null;
    return (
      <div className="h-full w-full flex flex-col">
        <ResultHeader
          isPdf={isPdf}
          currentPageIndex={currentPageIndex}
          totalPages={totalPages}
          isStreaming={isStreaming}
          onPrevPage={handlePrevPage}
          onNextPage={handleNextPage}
          onExportExcel={handleExportExcel}
          isExportingExcel={isExportingExcel}
          onExportPdf={handleExportPdf}
          isExportingPdf={isExportingPdf}
        />
        <div className="flex-1 flex items-center justify-center gap-2 font-sans text-sm text-ink-muted">
          <span className="h-1.5 w-1.5 rounded-full bg-action animate-pulse" />
          Transcription de la page {pageNumber} en cours…
        </div>
      </div>
    );
  }
  const { final_warning } = transcriptionResult;

  return (
    <EditingCellContext.Provider value={editingCell}>
    <CellDraftContext.Provider value={cellDraftContextValue}>
    <div className="h-full w-full flex flex-col">
      <ResultHeader
        isPdf={isPdf}
        currentPageIndex={currentPageIndex}
        totalPages={totalPages}
        isStreaming={isStreaming}
        onPrevPage={handlePrevPage}
        onNextPage={handleNextPage}
        onExportExcel={handleExportExcel}
        isExportingExcel={isExportingExcel}
        onExportPdf={handleExportPdf}
        isExportingPdf={isExportingPdf}
      />

      <div className="flex-1 min-h-0 flex flex-col w-full max-w-[1800px] mx-auto px-6 py-3">
      {final_warning && (
        <div className="mb-3 rounded-md bg-conf-mid-soft border border-line px-4 py-2.5 font-sans text-sm text-ink flex items-center gap-2 shrink-0">
          <svg className="h-4 w-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="var(--color-conf-mid)" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
          </svg>
          {final_warning}
        </div>
      )}

      <div className="flex-1 flex gap-4 min-h-0">
        <AnnotatedImagePanel
          isPdf={isPdf}
          currentPageIndex={currentPageIndex}
          imagePreviewUrl={imagePreviewUrl}
          imageContainerRef={imageContainerRef}
          blocks={blocks}
          selectedBlockId={selectedBlockId}
          draggingBlockId={draggingBlockId}
          dragOffset={dragOffset}
          getBlockPointerHandlers={getBlockPointerHandlers}
        />

        <ContentPanel
          blocks={blocks}
          pageNumber={pageNumber}
          selectedBlockId={selectedBlockId}
          editingBlockId={editingBlockId}
          editDraft={editDraft}
          onEditDraftChange={setEditDraft}
          onSelectBlock={scrollToBlock}
          onStartEditBlock={handleStartEditBlock}
          onConfirmEditBlock={handleConfirmEditBlock}
          onCancelEditBlock={handleCancelEditBlock}
          onRowClick={handleRowClick}
          onCellDoubleClick={handleCellDoubleClick}
          blockRefs={blockRefs}
        />
      </div>

      {/* Barre d'actions */}
      <div className="mt-3 flex items-center justify-center gap-3 shrink-0 pb-2">
        <button
          onClick={handleNewImage}
          className="h-10 px-4 rounded-sm border border-line-control bg-transparent font-sans text-sm text-ink cursor-pointer inline-flex items-center gap-2"
        >
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99" />
          </svg>
          Nouvelle image
        </button>
      </div>

      {pendingCorrection && (
        <CorrectionModal
          onSubmit={handleSubmitCorrection}
          onCancel={handleCancelCorrection}
          isSubmitting={isSubmittingCorrection}
        />
      )}
      </div>
    </div>
    </CellDraftContext.Provider>
    </EditingCellContext.Provider>
  );
}
