import {
  exportReportDocx,
  getCompleteness,
  getDossierDocuments,
  getExtraction,
  getReorganizationReport,
} from './api'
import type { Dossier, ExtractionEntry } from './types'
import { CERTAINTY_LABELS, PRESENCE_LABELS } from './components/CompletenessChecklist'
import { reorgReportEntriesToTree, treeToMarkdownFoldersOnly } from './components/OrganizedTree'

/** Construction du rapport d'analyse complet (.docx).
 *
 * Vit ici plutôt que dans `ExtractionSheet` : le rapport couvre tout le dossier
 * (arborescence, pièces, extraction, synthèse, audit), donc il se télécharge aussi
 * bien depuis l'onglet d'extraction que depuis celui de l'audit — qui est le vrai
 * moment « j'ai fini, donnez-moi le livrable ». Le module recharge ses données au
 * lieu de les recevoir en props : l'action est rare et délibérée, et cela évite de
 * faire transiter l'état de l'extraction à travers trois composants.
 */

function escapeMd(value: string): string {
  return value.replace(/\|/g, '\\|').replace(/\r?\n/g, ' ')
}

function formatDuration(startIso: string, endIso: string): string {
  const ms = new Date(endIso).getTime() - new Date(startIso).getTime()
  if (!Number.isFinite(ms) || ms <= 0) return '—'
  const totalMinutes = Math.round(ms / 60000)
  const hours = Math.floor(totalMinutes / 60)
  const minutes = totalMinutes % 60
  if (hours > 0) return `${hours} h ${String(minutes).padStart(2, '0')} min`
  if (minutes > 0) return `${minutes} min`
  return `${Math.round(ms / 1000)} s`
}

/** Retire le titre `# ...` en première ligne d'un rapport IA (synthèse projet, audit des
 * risques) avant de l'inclure sous un titre `##` déjà porté par la section englobante. */
function stripLeadingHeading(md: string): string {
  const lines = md.split('\n')
  if (lines[0]?.startsWith('# ')) {
    return lines.slice(1).join('\n').replace(/^\n+/, '')
  }
  return md
}

function downloadBlob(filename: string, blob: Blob) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

export async function telechargerRapportDocx(dossierId: string, dossier: Dossier): Promise<void> {
  const [reorgReport, completenessEntries, entries, documents] = await Promise.all([
    getReorganizationReport(dossierId).catch(() => null),
    getCompleteness(dossierId).catch(() => []),
    getExtraction(dossierId).catch(() => [] as ExtractionEntry[]),
    getDossierDocuments(dossierId).catch(() => []),
  ])

  const documentPathById = new Map<string, string>()
  documents.forEach((d) => documentPathById.set(d.id, d.relative_path))

  const treeMd = reorgReport
    ? treeToMarkdownFoldersOnly(reorgReportEntriesToTree(reorgReport.entries))
    : '_Arborescence non disponible._'

  const selectedPieces = completenessEntries.filter((e) => e.is_selected)
  const piecesMd =
    selectedPieces.length > 0
      ? [
          '| Pièce | Statut | Sûreté |',
          '|---|---|---|',
          ...selectedPieces.map(
            (p) =>
              `| ${escapeMd(p.libelle)} | ${PRESENCE_LABELS[p.final_presence ?? ''] ?? '—'} | ${CERTAINTY_LABELS[p.final_certainty ?? ''] ?? '—'} |`,
          ),
        ].join('\n')
      : '_Aucune pièce sélectionnée._'

  // L'API sert les champs dans l'ordre du schéma (`extraction_schema.yaml`), qui porte le
  // regroupement thématique : une Map préserve l'ordre d'insertion, donc on ne retrie ni les
  // sections ni les champs — un tri alphabétique casserait cet ordre métier.
  const bySection = new Map<string, ExtractionEntry[]>()
  const sectionLabels = new Map<string, string>()
  for (const entry of entries) {
    const list = bySection.get(entry.section) ?? []
    list.push(entry)
    bySection.set(entry.section, list)
    sectionLabels.set(entry.section, entry.section_libelle)
  }

  const sortedSections = [...bySection.keys()]
  const extractionMd =
    sortedSections.length > 0
      ? sortedSections
          .map((section) => {
            const rows = (bySection.get(section) ?? []).map((entry) => {
              const sources =
                entry.sources.map((s) => documentPathById.get(s.document_id) ?? s.filename).join(', ') || '—'
              return `| ${escapeMd(entry.libelle)} | ${escapeMd(entry.final_value ?? 'Non trouvée')} | ${escapeMd(sources)} |`
            })
            return [
              `### ${sectionLabels.get(section) ?? section}`,
              '',
              '| Donnée | Valeur | Sources |',
              '|---|---|---|',
              ...rows,
            ].join('\n')
          })
          .join('\n\n')
      : '_Aucune donnée extraite._'

  const duration = formatDuration(dossier.created_at, dossier.extraction_validated_at ?? dossier.updated_at)

  // La synthèse projet et l'audit des risques portent déjà leur propre titre `# ...` en
  // première ligne : on le retire pour que le titre de section `##` ci-dessous fasse
  // office de titre unique, cohérent avec le reste du rapport.
  const syntheseMd = dossier.synthese_projet_md
    ? stripLeadingHeading(dossier.synthese_projet_md)
    : '_Synthèse projet non générée._'
  const auditMd = dossier.audit_risques_md
    ? stripLeadingHeading(dossier.audit_risques_md)
    : '_Audit des risques non généré._'

  const md = `# Rapport d'analyse — ${dossier.original_filename}

Généré le ${new Date().toLocaleString('fr-FR')}
Temps de traitement du dossier : **${duration}**

## Arborescence proposée

${treeMd}

## Pièces — étape 2 (complétude)

${piecesMd}

## Extraction des données — étape 3

${extractionMd}

## Synthèse projet — Phase 1

${syntheseMd}

## Audit des risques — Phase 2

${auditMd}
`

  const safeName = dossier.original_filename.replace(/\.[^./]+$/, '').replace(/[^a-zA-Z0-9._-]+/g, '_')
  const blob = await exportReportDocx(dossierId, md)
  downloadBlob(`rapport_${safeName}.docx`, blob)
}
