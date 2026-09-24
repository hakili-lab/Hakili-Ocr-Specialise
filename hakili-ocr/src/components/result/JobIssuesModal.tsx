/**
 * components/result/JobIssuesModal.tsx
 * Modale unique couvrant les scénarios d'interruption/échec partiel d'un job PDF en
 * arrière-plan (erreur Anthropic fatale, annulation demandée, crash générique du job) — voir `ResultScreen.tsx` pour la logique de priorité
 * entre les variantes et le déclenchement. Un seul bouton OK : ne referme que la modale,
 * ne touche jamais aux pages déjà chargées dans `AppContext`.
 */
import type { ReactNode } from 'react';

type JobIssuesModalProps =
  | { kind: 'fatal'; reason: string; pagesDone: number; pagesTotal: number; onAcknowledge: () => void }
  | { kind: 'cancelled'; pagesDone: number; pagesTotal: number; onAcknowledge: () => void }
  | { kind: 'crash'; message: string; onAcknowledge: () => void };

export function JobIssuesModal(props: JobIssuesModalProps) {
  let title: string;
  let body: ReactNode;

  if (props.kind === 'fatal') {
    title = 'Traitement interrompu';
    body = (
      <p className="font-sans text-sm text-ink-muted">
        {props.reason} {props.pagesDone}/{props.pagesTotal} pages ont pu être transcrites.
      </p>
    );
  } else if (props.kind === 'cancelled') {
    title = 'Transcription annulée';
    body = (
      <p className="font-sans text-sm text-ink-muted">
        {props.pagesDone}/{props.pagesTotal} pages ont pu être transcrites avant l'annulation.
      </p>
    );
  } else {
    title = 'Une erreur est survenue';
    body = <p className="font-sans text-sm text-ink-muted">{props.message}</p>;
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/30 backdrop-blur-sm animate-edit-fade">
      <div className="w-full max-w-md rounded-md bg-surface p-5 shadow-float mx-4">
        <h3 className="font-display font-normal text-lg text-ink mb-2">{title}</h3>
        {body}
        <div className="mt-4 flex items-center justify-end">
          <button
            type="button"
            onClick={props.onAcknowledge}
            className="h-8 px-4 rounded-sm border-0 bg-action text-surface font-sans text-xs font-medium cursor-pointer"
          >
            OK
          </button>
        </div>
      </div>
    </div>
  );
}
