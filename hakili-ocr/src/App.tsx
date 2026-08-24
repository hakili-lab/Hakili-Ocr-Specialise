/**
 * App.tsx
 * Racine applicative : routage des 4 écrans (`state.currentScreen`, pas de
 * router — un seul flux linéaire upload → preview → loading → result) et
 * connexion entre `AppContext` (état) et `useTranscription` (réseau).
 */
import { useEffect } from 'react';
import { useApp } from './context/AppContext';
import { useTranscription } from './hooks/useTranscribe';
import UploadScreen from './components/UploadScreen';
import PreviewScreen from './components/PreviewScreen';
import LoadingScreen from './components/LoadingScreen';
import ResultScreen from './components/ResultScreen';

export default function App() {
  const { state, dispatch } = useApp();
  const { start, isPending, isError, error, progress, data } = useTranscription();

  // Déclenche la transcription dès qu'on entre sur l'écran de chargement avec un fichier prêt.
  useEffect(() => {
    if (state.uploadedImage && state.currentScreen === 'loading') {
      start(state.uploadedImage);
    }
  }, [state.uploadedImage, state.currentScreen, start]);

  // Fait suivre le résultat de useTranscription() dans AppContext dès qu'il arrive.
  // Pour un PDF, `data` peut arriver en plusieurs vagues (pages ajoutées au fil du
  // traitement en arrière-plan, voir useTranscribe.ts) : la toute première vague bascule
  // l'écran vers 'result' (SET_RESULT), les suivantes ajoutent les nouvelles pages sans
  // perturber la page actuellement affichée ni les éditions déjà faites (MERGE_PDF_RESULT).
  useEffect(() => {
    if (!data) return;
    if ('blocks' in data) {
      // Image simple : payload complet en un seul morceau, pas de flux à gérer.
      dispatch({ type: 'SET_RESULT', result: data });
      return;
    }
    const pagesTotal = progress?.pagesTotal ?? null;
    if (state.pdfResult) {
      dispatch({ type: 'MERGE_PDF_RESULT', result: data, pagesTotal });
    } else {
      // Normalise pour que le type reste cohérent avec SET_RESULT (voir sa définition) —
      // un payload PDF n'a pas de champ `blocks` propre. Passé via une variable (pas un
      // littéral inline) pour éviter l'excess-property-check de TS sur `pages`.
      const normalizedResult = { ...data, blocks: [] };
      dispatch({ type: 'SET_RESULT', result: normalizedResult, pagesTotal });
    }
    // state.pdfResult est lu volontairement sans figurer ici : on veut réagir uniquement
    // aux nouvelles arrivées de `data`, pas aux mises à jour de pdfResult qu'on vient
    // nous-mêmes de déclencher (qui refléteraient sinon un état d'un cran en retard).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, dispatch]);

  // Bascule sur l'écran de chargement dès qu'une requête part, même si CONFIRM_UPLOAD
  // (déclenché par PreviewScreen) n'a pas encore eu le temps de le faire lui-même.
  useEffect(() => {
    if (isPending && state.currentScreen !== 'loading') {
      dispatch({ type: 'NAVIGATE', screen: 'loading' });
    }
  }, [isPending, state.currentScreen, dispatch]);

  return (
    <div className="h-screen w-screen overflow-hidden flex flex-col">
      {/* Header — l'écran Résultat gère le sien (navigation de pages, export...) */}
      {state.currentScreen !== 'result' && (
        <header className="flex items-center gap-2.5 px-6 sm:gap-3 sm:px-8 shrink-0 h-14 bg-surface-page border-b border-line">
          <img src="/hakili-mark-512.png" alt="" className="h-8 w-8 object-contain" />
          <span className="font-sans font-semibold text-base tracking-[0.08em] text-ink">HAKILI</span>
          <span className="w-px h-4 bg-line inline-block" />
          <span className="font-mono font-normal text-xs text-ink-muted">OCR</span>
          <div className="flex-1" />
          {state.currentScreen === 'preview' && (
            <button
              type="button"
              onClick={() => dispatch({ type: 'CANCEL_PREVIEW' })}
              className="font-sans font-medium text-base text-ink-muted cursor-pointer bg-transparent border-0"
            >
              Annuler
            </button>
          )}
          {state.currentScreen === 'loading' && !isError && (
            <span className="font-sans font-medium text-base text-ink-muted opacity-40 cursor-not-allowed">
              Annuler
            </span>
          )}
        </header>
      )}

      {/* Contenu */}
      <main className="flex-1 overflow-hidden">
        {state.currentScreen === 'upload' && (
          <div className="h-full flex items-center justify-center">
            <UploadScreen />
          </div>
        )}
        {state.currentScreen === 'preview' && (
          <div className="h-full flex items-center justify-center">
            <PreviewScreen />
          </div>
        )}
        {state.currentScreen === 'loading' && (
          <div className="h-full flex items-center justify-center">
            <LoadingScreen isError={isError} error={error} />
          </div>
        )}
        {state.currentScreen === 'result' && state.transcriptionResult && (
          <ResultScreen progress={progress} />
        )}
      </main>
    </div>
  );
}