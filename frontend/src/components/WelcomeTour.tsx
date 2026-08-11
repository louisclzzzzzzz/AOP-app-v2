import { useState } from 'react'
import { markTourSeen } from '../tour'
import { BTN_PRIMAIRE } from '../ui'

interface Props {
  onDone: () => void
}

interface TourStep {
  title: string
  body: string
}

const STEPS: TourStep[] = [
  {
    title: 'Bienvenue sur AOP',
    body: 'AOP analyse vos dossiers de consultation des entreprises (DCE) grâce à l’intelligence artificielle : déposez un fichier .zip, l’application s’occupe du reste. Ce court tutoriel présente les grandes étapes en quelques pages.',
  },
  {
    title: 'Étape 1 — Classification',
    body: 'Chaque document est automatiquement classé et proposé dans une arborescence organisée par catégorie et par lot. Vous pouvez corriger le classement avant d’appliquer la copie triée — la source d’origine n’est jamais modifiée.',
  },
  {
    title: 'Étape 2 — Complétude',
    body: 'L’application vérifie que les pièces attendues pour un dossier assurance construction (RC, CCAP, RICT, étude de sol…) sont bien présentes, et localise où les retrouver dans les documents.',
  },
  {
    title: 'Étape 3 — Extraction',
    body: 'Les données clés du dossier (montants, garanties, délais, intervenants…) sont extraites avec leurs sources, et recoupées entre plusieurs documents quand c’est possible.',
  },
  {
    title: 'Synthèse, audit et rapport',
    body: 'Générez une synthèse narrative du projet puis un audit des risques (croisant les données publiques Géorisques), et téléchargez un rapport Word complet ainsi que le tableau d’extraction au format Excel.',
  },
]

export function WelcomeTour({ onDone }: Props) {
  const [stepIndex, setStepIndex] = useState(0)
  const isLast = stepIndex === STEPS.length - 1
  const step = STEPS[stepIndex]

  const finish = () => {
    markTourSeen()
    onDone()
  }

  return (
    <div className="fixed inset-0 z-40 flex min-h-screen items-center justify-center bg-surface-2 px-6">
      <div className="w-full max-w-lg rounded-xl border border-bord bg-white p-8 shadow-sm">
        <p className="text-xs font-medium uppercase tracking-wide text-encre-3">
          {stepIndex + 1} / {STEPS.length}
        </p>
        <h1 className="mt-1 text-xl font-semibold text-encre">{step.title}</h1>
        <p className="mt-3 text-sm leading-relaxed text-encre-2">{step.body}</p>

        <div className="mt-6 flex items-center justify-center gap-1.5">
          {STEPS.map((s, i) => (
            <span
              key={s.title}
              className={`h-1.5 rounded-full transition-all ${
                i === stepIndex ? 'w-5 bg-ardoise' : 'w-1.5 bg-bord'
              }`}
            />
          ))}
        </div>

        <div className="mt-6 flex items-center justify-between">
          <button type="button" onClick={finish} className="text-sm text-encre-3 hover:text-encre-2">
            Passer le tutoriel
          </button>
          <div className="flex items-center gap-2">
            {stepIndex > 0 && (
              <button
                type="button"
                onClick={() => setStepIndex((i) => i - 1)}
                className="rounded-md border border-bord-fort px-3 py-2 text-sm font-medium text-encre-2 hover:bg-surface-2"
              >
                Précédent
              </button>
            )}
            <button
              type="button"
              onClick={isLast ? finish : () => setStepIndex((i) => i + 1)}
              className={BTN_PRIMAIRE}
            >
              {isLast ? 'Commencer' : 'Suivant'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
