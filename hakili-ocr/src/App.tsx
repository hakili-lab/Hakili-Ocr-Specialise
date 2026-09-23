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
  const { start, isPending, isError, error, progress, data, fatalError, failedPages, isConnectionIssue, cancelReason, cancel } =
    useTranscription();

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
    // Tant qu'aucune page n'a encore réussi, ne jamais router vers l'écran résultat — un
    // job PDF peut exposer un résultat partiel avec `pages: []` avant que `status` ne
    // passe à `"done"`/`"error"` (une erreur fatale ou des pages en échec dès le début du
    // traitement, voir `useTranscribe.ts`/`fatalError`/`failedPages`). `SET_RESULT` avec
    // `pages: []` laisserait `transcriptionResult` à `null`, et cet écran ne rend
    // `ResultScreen` que si `transcriptionResult` est non-null — sans ce garde, l'app
    // resterait bloquée sur un écran vide. Dans ce cas précis (aucune page encore
    // réussie), `LoadingScreen` reste affiché avec son message d'erreur générique
    // existant (`isError`/`error`, basé sur `job.error` une fois le job finalisé) —
    // le modal dédié `JobIssuesModal` ne prend le relais qu'une fois sur l'écran résultat.
    if (data.pages.length === 0) return;
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
            <button
              type="button"
              onClick={() => {
                // Rien à afficher tant qu'aucune page n'est encore prête (voir le garde sur
                // `data.pages.length === 0` plus haut) — annuler ici renvoie directement à
                // l'écran de dépôt plutôt que d'attendre une confirmation qui n'aurait rien à
                // montrer. `cancel()` est un no-op silencieux si aucun job PDF n'est actif
                // (image simple, ou job pas encore créé).
                cancel();
                dispatch({ type: 'RESET' });
              }}
              className="font-sans font-medium text-base text-ink-muted cursor-pointer bg-transparent border-0"
            >
              Annuler
            </button>
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
        {/*
          Ne PAS ajouter `&& state.transcriptionResult` ici : `currentScreen` ne passe à
          `'result'` que lorsque `transcriptionResult` est déjà garanti non-null (via
          SET_RESULT, dont le déclenchement est lui-même gardé plus haut), mais une fois
          sur cet écran, `SET_PAGE` (navigation vers une page pas encore transcrite, voir
          AppContext.tsx) remet légitimement `transcriptionResult` à `null` — un tel garde
          démonterait alors `ResultScreen` entièrement, alors que c'est justement ce
          composant qui sait afficher le placeholder "page en cours"/"page en échec" pour
          ce cas. Avec le garde, l'écran devient blanc silencieusement (bug vérifié et
          corrigé) ; sans lui, ResultScreen gère `transcriptionResult === null` lui-même.
        */}
        {state.currentScreen === 'result' && (
          <ResultScreen
            progress={progress}
            isError={isError}
            error={error}
            fatalError={fatalError}
            failedPages={failedPages}
            isConnectionIssue={isConnectionIssue}
            cancelReason={cancelReason}
            onCancel={cancel}
          />
        )}
      </main>
    </div>
  );
}