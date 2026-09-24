/**
 * components/result/ResultHeader.tsx
 * Header de l'écran Résultat : logo, navigation de pages (PDF), actions
 * Exporter Excel/PDF. Purement présentationnel — aucun état propre.
 */
type ResultHeaderProps = {
  isPdf: boolean;
  /**
   * Navigation uniquement parmi les pages déjà affichables (prêtes dans leur ordre d'arrivée,
   * puis les pages en échec) — voir `navPageNumbers` dans ResultScreen.tsx. Aucun compteur
   * "page X / Y" n'est affiché : seuls les boutons précédent/suivant, activés selon ces deux
   * drapeaux.
   */
  hasPrev: boolean;
  hasNext: boolean;
  /** Le document a encore des pages en cours de traitement en arrière-plan. */
  isStreaming: boolean;
  onPrevPage: () => void;
  onNextPage: () => void;
  onExportExcel: () => void;
  isExportingExcel: boolean;
  onExportPdf: () => void;
  isExportingPdf: boolean;
  /** Demande l'arrêt du job en cours — bouton affiché seulement tant que `isStreaming`. */
  onCancel: () => void;
};

/** Logo + navigation de pages (PDF) + actions (Annuler / Exporter Excel / Exporter PDF). */
export function ResultHeader({
  isPdf,
  hasPrev,
  hasNext,
  isStreaming,
  onPrevPage,
  onNextPage,
  onExportExcel,
  isExportingExcel,
  onExportPdf,
  isExportingPdf,
  onCancel,
}: ResultHeaderProps) {
  return (
    <header className="flex items-center gap-2.5 px-6 sm:gap-4 sm:px-8 shrink-0 h-14 bg-surface-page border-b border-line">
      <img src="/hakili-mark-512.png" alt="" className="h-8 w-8 object-contain" />
      <span className="font-sans font-semibold text-base tracking-[0.08em] text-ink">HAKILI</span>
      <span className="w-px h-4 bg-line inline-block" />
      <span className="font-mono font-normal text-xs text-ink-muted">OCR</span>
      <div className="flex-1" />
      {isPdf && (
        <span className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={onPrevPage}
            disabled={!hasPrev}
            className="flex items-center justify-center cursor-pointer disabled:opacity-30 disabled:cursor-not-allowed bg-transparent border-0 p-1"
            aria-label="Page précédente"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--color-ink-secondary)" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
              <path d="M15 18l-6-6 6-6" />
            </svg>
          </button>
          <button
            type="button"
            onClick={onNextPage}
            disabled={!hasNext}
            className="flex items-center justify-center cursor-pointer disabled:opacity-30 disabled:cursor-not-allowed bg-transparent border-0 p-1"
            aria-label="Page suivante"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--color-ink-secondary)" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
              <path d="M9 18l6-6-6-6" />
            </svg>
          </button>
          {isStreaming && (
            <span
              className="flex items-center gap-1.5 pl-1 font-sans text-xs text-ink-muted"
              title="Les pages restantes continuent d'être transcrites en arrière-plan"
            >
              <span className="h-1.5 w-1.5 rounded-full bg-action animate-pulse" />
              Transcription en cours…
            </span>
          )}
          {isStreaming && (
            <button
              type="button"
              onClick={onCancel}
              className="h-7 px-2.5 ml-1 rounded-sm border border-line-control bg-transparent font-sans text-xs text-ink-muted cursor-pointer"
              title="Arrête le traitement des pages pas encore transcrites — celles déjà obtenues restent disponibles"
            >
              Annuler
            </button>
          )}
        </span>
      )}
      {isPdf && <span className="w-px h-4 bg-line inline-block" />}
      <button
        type="button"
        onClick={onExportExcel}
        disabled={isExportingExcel || isStreaming}
        title={isStreaming ? 'Attendez la fin de la transcription pour exporter le document complet' : undefined}
        className="h-10 px-4 rounded-sm border border-line-control bg-transparent font-sans font-medium text-base text-ink cursor-pointer inline-flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {isExportingExcel && (
          <svg className="h-4 w-4 animate-spin" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth={4} />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
          </svg>
        )}
        {isExportingExcel ? 'Génération…' : 'Exporter en Excel'}
      </button>
      <button
        type="button"
        onClick={onExportPdf}
        disabled={isExportingPdf || isStreaming}
        title={isStreaming ? 'Attendez la fin de la transcription pour exporter le document complet' : undefined}
        className="h-10 px-4 rounded-sm border-0 bg-action text-surface font-sans font-medium text-base cursor-pointer inline-flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {isExportingPdf && (
          <svg className="h-4 w-4 animate-spin" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth={4} />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
          </svg>
        )}
        {isExportingPdf ? 'Génération…' : 'Exporter en PDF'}
      </button>
    </header>
  );
}
