/**
 * components/LoadingScreen.tsx
 * Écran 3/4 : anneau de progression indéterminé (spinner) + ligne de statut
 * animée + barre de balayage. Affiché tant qu'aucune page n'est encore prête
 * — dès que la première page arrive, `App.tsx` bascule vers `ResultScreen`
 * (`SET_RESULT`), qui a son propre indicateur "Transcription en cours…" pour
 * les pages suivantes (voir `ResultHeader.tsx`). Volontairement pas de
 * compteur "Page X / Y" ici : la progression réelle par page n'est pas
 * affichée avant que la première page ne soit visible à l'écran.
 * Gère aussi l'état d'erreur final (échec réseau ou backend), avec un bouton
 * pour revenir à l'écran de dépôt.
 */
import { useEffect, useState } from 'react';
import { TranscribeError } from '../hooks/useTranscribe';
import { useApp } from '../context/AppContext';

interface LoadingScreenProps {
  isError: boolean;
  error: TranscribeError | null;
}

// Étapes illustratives affichées en rotation — pas un vrai suivi du backend.
const STEPS = [
  'Lecture des écritures…',
  'Repérage des tableaux…',
  'Vérification des formules…',
  'Mise en forme du texte…',
];

const STEP_INTERVAL_MS = 2500;
const STEP_FADE_MS = 300;

const RING_SIZE = 120;
const RING_STROKE = 6;
const RING_RADIUS = (RING_SIZE - RING_STROKE) / 2;
const RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS;

const BAR_WIDTH = 280;

/**
 * Reflète `prefers-reduced-motion`, avec écoute des changements à chaud. Ne
 * couvre que la rotation du texte de statut (pilotée par `setInterval`) — les
 * animations CSS (anneau, barre) sont déjà neutralisées par la règle globale
 * `@media (prefers-reduced-motion: reduce)` dans `index.css`.
 */
function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () => typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );
  useEffect(() => {
    const mql = window.matchMedia('(prefers-reduced-motion: reduce)');
    const onChange = () => setReduced(mql.matches);
    mql.addEventListener('change', onChange);
    return () => mql.removeEventListener('change', onChange);
  }, []);
  return reduced;
}

export default function LoadingScreen({ isError, error }: LoadingScreenProps) {
  const { dispatch } = useApp();
  const reducedMotion = usePrefersReducedMotion();

  const [stepIndex, setStepIndex] = useState(0);
  const [isStepVisible, setIsStepVisible] = useState(true);

  // Rotation des textes d'étape toutes les STEP_INTERVAL_MS, avec un fondu
  // sortant/entrant de STEP_FADE_MS de chaque côté du changement de texte.
  // Le double requestAnimationFrame force un repaint avec opacity:0 avant de
  // repasser à 1, pour que la transition CSS se rejoue à chaque cycle.
  useEffect(() => {
    if (reducedMotion) return;
    const interval = setInterval(() => {
      setIsStepVisible(false);
      window.setTimeout(() => {
        setStepIndex((prev) => (prev + 1) % STEPS.length);
        requestAnimationFrame(() => requestAnimationFrame(() => setIsStepVisible(true)));
      }, STEP_FADE_MS);
    }, STEP_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [reducedMotion]);

  if (isError && error) {
    return (
      <div className="w-full h-full bg-surface-sunken trame-points flex flex-col items-center justify-center gap-6">
        <div className="h-16 w-16 rounded-full bg-conf-low-soft flex items-center justify-center">
          <svg className="h-8 w-8" fill="none" viewBox="0 0 24 24" stroke="var(--color-conf-low)" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
          </svg>
        </div>

        <div className="text-center">
          <h2 className="font-display font-normal text-2xl text-ink mb-2">Une erreur est survenue</h2>
          <p className="font-sans text-base text-ink-muted max-w-md">{error.message}</p>
        </div>

        <button
          type="button"
          onClick={() => dispatch({ type: 'NAVIGATE', screen: 'upload' })}
          className="h-12 px-6 rounded-sm border-0 bg-action text-surface font-sans font-medium text-base cursor-pointer"
        >
          Réessayer
        </button>
      </div>
    );
  }

  return (
    <div className="w-full h-full bg-surface-sunken trame-points flex flex-col items-center justify-center gap-6 p-8">
      <ProgressRing />

      <div className="h-5 flex items-center justify-center">
        <span
          className="font-sans text-[13px] text-ink-muted"
          style={{
            opacity: isStepVisible ? 1 : 0,
            transition: `opacity ${STEP_FADE_MS}ms var(--ease-out-hk)`,
          }}
        >
          {STEPS[stepIndex]}
        </span>
      </div>

      <ProgressBar />
    </div>
  );
}

/** Arc partiel qui tourne indéfiniment (spinner), icône document au centre. */
function ProgressRing() {
  return (
    <div className="relative shrink-0" style={{ width: RING_SIZE, height: RING_SIZE }}>
      <svg
        width={RING_SIZE}
        height={RING_SIZE}
        viewBox={`0 0 ${RING_SIZE} ${RING_SIZE}`}
        className="animate-loading-ring-spin"
      >
        <circle cx={RING_SIZE / 2} cy={RING_SIZE / 2} r={RING_RADIUS} fill="none" stroke="var(--color-line)" strokeWidth={RING_STROKE} />
        <circle
          cx={RING_SIZE / 2}
          cy={RING_SIZE / 2}
          r={RING_RADIUS}
          fill="none"
          stroke="var(--color-action)"
          strokeWidth={RING_STROKE}
          strokeLinecap="round"
          strokeDasharray={`${RING_CIRCUMFERENCE * 0.25} ${RING_CIRCUMFERENCE}`}
        />
      </svg>
      <div className="absolute inset-0 flex items-center justify-center">
        <svg width="24" height="24" viewBox="0 0 40 40" fill="none" stroke="var(--color-ink-muted)" strokeWidth={1.5}>
          <path d="M10 4h14l6 6v24a2 2 0 0 1-2 2H10a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z" />
          <path d="M24 4v6h6" />
          <line x1="12" y1="20" x2="26" y2="20" />
          <line x1="12" y1="25" x2="26" y2="25" />
          <line x1="12" y1="30" x2="20" y2="30" />
        </svg>
      </div>
    </div>
  );
}

/**
 * Remplissage indéterminé : un segment qui balaie la piste de gauche à droite
 * puis revient (cf. keyframes hk-loading-bar-sweep, index.css), en boucle
 * jusqu'à ce que la première page soit prête.
 */
function ProgressBar() {
  return (
    <div className="relative h-[3px] rounded-full overflow-hidden bg-line" style={{ width: BAR_WIDTH }}>
      <div className="absolute inset-y-0 w-[30%] bg-action rounded-full animate-loading-bar-sweep" />
    </div>
  );
}
