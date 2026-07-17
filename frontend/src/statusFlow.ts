import type { DossierStatus } from './types'

/** Ordre chronologique du pipeline. `error` en est exclu : un dossier en erreur
 * n'est pas considéré comme ayant atteint/dépassé une étape donnée. */
const STATUS_ORDER: DossierStatus[] = [
  'uploaded',
  'unzipping',
  'inventorying',
  'extracting_text',
  'ready_step1',
  'classifying',
  'classified',
  'reorganizing',
  'reorganized',
  'analyzing_completeness',
  'completeness_review',
  'completeness_validated',
  'extracting',
  'extraction_review',
  'extraction_validated',
]

/** Vrai si `status` a atteint ou dépassé `target` dans le pipeline — permet à une étape
 * de rester consultable (en lecture seule) une fois le dossier passé aux étapes suivantes. */
export function isAtOrAfter(status: DossierStatus, target: DossierStatus): boolean {
  const statusIndex = STATUS_ORDER.indexOf(status)
  const targetIndex = STATUS_ORDER.indexOf(target)
  if (statusIndex === -1 || targetIndex === -1) return false
  return statusIndex >= targetIndex
}
