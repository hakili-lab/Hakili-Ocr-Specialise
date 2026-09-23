/**
 * context/AppContext.tsx
 * État global de l'application : un seul `useReducer` piloté par `AppAction`
 * (types/index.ts), exposé via `useApp()`. Tient lieu de "store" — pas de
 * Redux/Zustand, l'app n'a qu'un seul flux d'écrans linéaire (upload → preview
 * → loading → result) qui ne justifie pas plus.
 */
import React, { createContext, useContext, useReducer, type ReactNode } from 'react';
import type {
  AppState,
  AppAction,
  PageResult,
  PDFTranscriptionResult,
  TranscriptionPayload,
} from '../types';

const initialState: AppState = {
  currentScreen: 'upload',
  uploadedImage: null,
  imagePreviewUrl: null,
  transcriptionResult: null,
  selectedBlockId: null,
  pdfResult: null,
  currentPageIndex: 0,
  pdfPagesTotal: null,
};

/** Distingue les deux formes de payload que `SET_RESULT` peut recevoir (image seule vs PDF multi-pages). */
function isPDFResult(payload: TranscriptionPayload): payload is PDFTranscriptionResult {
  return 'pages' in payload;
}

/**
 * Retrouve la page d'un numéro donné dans `pages` — jamais par index de tableau : les
 * pages sont traitées en parallèle côté backend et peuvent arriver dans n'importe quel
 * ordre, donc `pages[i]` ne correspond pas forcément à la page `i + 1`.
 */
function findPageByNumber(pages: PageResult[], pageNumber: number): PageResult | undefined {
  return pages.find((p) => p.page_number === pageNumber);
}

/**
 * Reducer applicatif. `UPDATE_BLOCK_MARKDOWN`/`UPDATE_BLOCK_BBOX` dupliquent la
 * même structure de mise à jour (met à jour `transcriptionResult.blocks` et,
 * si un PDF est chargé, la page courante dans `pdfResult.pages`) — assumé tel
 * quel plutôt que factorisé prématurément pour deux occurrences seulement.
 */
function appReducer(state: AppState, action: AppAction): AppState {
  switch (action.type) {
    case 'NAVIGATE':
      return { ...state, currentScreen: action.screen };

    case 'SET_IMAGE':
      return {
        ...state,
        uploadedImage: action.file,
        imagePreviewUrl: action.previewUrl,
        currentScreen: 'preview',
        pdfResult: null,
        currentPageIndex: 0,
        pdfPagesTotal: null,
      };

    case 'CONFIRM_UPLOAD':
      return {
        ...state,
        uploadedImage: action.file,
        imagePreviewUrl: action.previewUrl ?? state.imagePreviewUrl,
        currentScreen: 'loading',
      };

    case 'CANCEL_PREVIEW':
      return {
        ...state,
        uploadedImage: null,
        imagePreviewUrl: null,
        currentScreen: 'upload',
      };

    case 'SET_RESULT': {
      const payload = action.result;
      if (isPDFResult(payload)) {
        // La première page arrivée n'est pas forcément la page 1 (les pages sont
        // transcrites en parallèle et peuvent finir dans n'importe quel ordre) — on
        // affiche celle qui est réellement prête, avec son propre numéro, plutôt que de
        // forcer l'affichage sur la page 1.
        const firstPage = payload.pages[0] ?? null;
        return {
          ...state,
          pdfResult: payload,
          transcriptionResult: firstPage?.ocr ?? null,
          imagePreviewUrl: firstPage
            ? `data:${firstPage.media_type};base64,${firstPage.image_b64}`
            : null,
          currentScreen: 'result',
          selectedBlockId: null,
          currentPageIndex: (firstPage?.page_number ?? 1) - 1,
          pdfPagesTotal: action.pagesTotal ?? payload.pages.length,
        };
      }
      return {
        ...state,
        transcriptionResult: payload,
        pdfResult: null,
        currentScreen: 'result',
        selectedBlockId: null,
        currentPageIndex: 0,
        pdfPagesTotal: null,
      };
    }

    // Ajoute les pages nouvellement prêtes à un `pdfResult` déjà affiché — dispatché à
    // chaque poll une fois l'écran 'result' déjà atteint (voir App.tsx). `action.result.pages`
    // est garanti être une extension du contenu SERVEUR déjà connu (le contenu original
    // d'une page ne change jamais une fois transcrite côté backend) — MAIS ne jamais
    // remplacer une page déjà présente localement par sa version entrante : elle peut
    // porter une édition utilisateur (`UPDATE_BLOCK_MARKDOWN`/`UPDATE_BLOCK_BBOX`) non
    // envoyée au serveur, qu'un remplacement brut écraserait silencieusement — y compris
    // dans l'export PDF/Excel, qui lit `pdfResult.pages` directement. On ne fusionne donc
    // que les NOUVELLES pages (numéro pas encore présent localement).
    case 'MERGE_PDF_RESULT': {
      if (!state.pdfResult) return state;
      const incomingPages = action.result.pages;
      const pagesTotal = action.pagesTotal ?? state.pdfPagesTotal;
      if (incomingPages.length <= state.pdfResult.pages.length) {
        return pagesTotal === state.pdfPagesTotal ? state : { ...state, pdfPagesTotal: pagesTotal };
      }

      const existingPageNumbers = new Set(state.pdfResult.pages.map((p) => p.page_number));
      const newlyArrivedPages = incomingPages.filter((p) => !existingPageNumbers.has(p.page_number));
      const mergedPages = [...state.pdfResult.pages, ...newlyArrivedPages];

      // La page actuellement affichée a pu ne pas être prête au moment du dernier rendu
      // (l'utilisateur a navigué vers un numéro de page pas encore transcrit, voir
      // `SET_PAGE`) — si elle vient tout juste d'arriver dans ce poll, on rafraîchit son
      // contenu pour que le placeholder "en cours" se transforme en résultat sans action
      // supplémentaire de l'utilisateur. Ne touche à rien si la page était déjà chargée
      // (protège une édition en cours) ou si elle n'est toujours pas prête.
      const currentPageNumber = state.currentPageIndex + 1;
      const nowCurrentPage = existingPageNumbers.has(currentPageNumber)
        ? undefined
        : newlyArrivedPages.find((p) => p.page_number === currentPageNumber);

      return {
        ...state,
        pdfResult: {
          ...state.pdfResult,
          pages: mergedPages,
          final_warning: action.result.final_warning ?? state.pdfResult.final_warning,
        },
        pdfPagesTotal: pagesTotal,
        ...(nowCurrentPage
          ? {
              transcriptionResult: nowCurrentPage.ocr,
              imagePreviewUrl: `data:${nowCurrentPage.media_type};base64,${nowCurrentPage.image_b64}`,
            }
          : {}),
      };
    }

    case 'SET_PAGE': {
      if (!state.pdfResult) return state;
      // La page ciblée n'est pas forcément déjà transcrite (l'utilisateur peut naviguer
      // vers n'importe quel numéro jusqu'à `pdfPagesTotal`) — si elle n'est pas encore
      // prête, `transcriptionResult`/`imagePreviewUrl` passent à `null` et l'écran affiche
      // un placeholder "en cours" (voir ResultScreen.tsx) plutôt que de refuser de naviguer.
      const page = findPageByNumber(state.pdfResult.pages, action.pageNumber);
      return {
        ...state,
        currentPageIndex: action.pageNumber - 1,
        transcriptionResult: page?.ocr ?? null,
        imagePreviewUrl: page ? `data:${page.media_type};base64,${page.image_b64}` : null,
        selectedBlockId: null,
      };
    }

    case 'SELECT_BLOCK':
      return { ...state, selectedBlockId: action.blockId };

    case 'UPDATE_BLOCK_MARKDOWN': {
      if (!state.transcriptionResult) return state;
      const updatedBlocks = state.transcriptionResult.blocks.map((b) =>
        b.id === action.blockId ? { ...b, markdown: action.markdown } : b
      );
      const newTranscriptionResult = { ...state.transcriptionResult, blocks: updatedBlocks };

      // Retrouve la page à mettre à jour par son NUMÉRO (currentPageIndex + 1), pas par
      // position dans le tableau : les pages peuvent être arrivées dans n'importe quel
      // ordre, donc `pdfResult.pages[currentPageIndex]` ne désigne pas forcément la page
      // affichée.
      let newPdfResult = state.pdfResult;
      if (state.pdfResult) {
        const currentPageNumber = state.currentPageIndex + 1;
        const updatedPages = state.pdfResult.pages.map((p) =>
          p.page_number === currentPageNumber ? { ...p, ocr: newTranscriptionResult } : p
        );
        newPdfResult = { ...state.pdfResult, pages: updatedPages };
      }

      return {
        ...state,
        transcriptionResult: newTranscriptionResult,
        pdfResult: newPdfResult,
      };
    }

    case 'UPDATE_BLOCK_BBOX': {
      if (!state.transcriptionResult) return state;
      const updatedBlocks = state.transcriptionResult.blocks.map((b) =>
        b.id === action.blockId ? { ...b, bbox: action.bbox } : b
      );
      const newTranscriptionResult = { ...state.transcriptionResult, blocks: updatedBlocks };

      // Même remarque que UPDATE_BLOCK_MARKDOWN ci-dessus : recherche par numéro de page,
      // pas par position dans le tableau.
      let newPdfResult = state.pdfResult;
      if (state.pdfResult) {
        const currentPageNumber = state.currentPageIndex + 1;
        const updatedPages = state.pdfResult.pages.map((p) =>
          p.page_number === currentPageNumber ? { ...p, ocr: newTranscriptionResult } : p
        );
        newPdfResult = { ...state.pdfResult, pages: updatedPages };
      }

      return {
        ...state,
        transcriptionResult: newTranscriptionResult,
        pdfResult: newPdfResult,
      };
    }

    case 'RESET':
      return { ...initialState };

    default:
      return state;
  }
}

interface AppContextValue {
  state: AppState;
  dispatch: React.Dispatch<AppAction>;
}

const AppContext = createContext<AppContextValue | null>(null);

/** Accès à `{ state, dispatch }` depuis n'importe quel composant sous `<AppProvider>`. */
export function useApp() {
  const context = useContext(AppContext);
  if (!context) {
    throw new Error("useApp doit être utilisé à l'intérieur d'un AppProvider");
  }
  return context;
}

interface AppProviderProps {
  children: ReactNode;
}

/** Fournit `AppContext` à tout l'arbre — monté une seule fois dans `main.tsx`. */
export function AppProvider({ children }: AppProviderProps) {
  const [state, dispatch] = useReducer(appReducer, initialState);

  return (
    <AppContext.Provider value={{ state, dispatch }}>
      {children}
    </AppContext.Provider>
  );
}