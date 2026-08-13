import { createElement, type ReactNode } from 'react'
import type { Citation } from '../types'
import { TexteCite } from './CitationChip'

interface Props {
  text: string
  /** Registre des citations du rapport. Fourni, les marqueurs `⟦cite:cN⟧` du texte deviennent des
   * pastilles cliquables ; absent, ils sont simplement absents du texte (rapport sans citations). */
  citations?: Record<string, Citation>
  onOpenCitation?: (citation: Citation) => void
  documentActif?: string | null
}

/** Rendu Markdown minimal, sans dépendance externe — couvre le sous-ensemble produit par les
 * rapports IA (titres `#` à `######`, tableaux GFM, listes à puces/numérotées avec sous-puces
 * indentées, gras `**...**`, paragraphes) : pas un parseur CommonMark généraliste.
 *
 * Ligne par ligne plutôt que bloc par bloc (blocs séparés par une ligne vide) : le LLM enchaîne
 * parfois plusieurs titres consécutifs sans ligne vide entre eux (ex. "### Objectifs\n####
 * Environnementaux\ntexte..."), ce qu'un découpage par bloc fusionnerait en un seul paragraphe et
 * afficherait les `#`/`##` littéralement au lieu de les styler. */
export function Markdown({ text, citations, onOpenCitation, documentActif }: Props) {
  // Le rendu inline est injecté plutôt que codé en dur : c'est le SEUL point qui change entre un
  // rapport avec citations et un rapport sans, et le reste du parseur (titres, tableaux, listes)
  // n'a pas à connaître leur existence.
  const renderInline: RenderInline =
    citations && onOpenCitation
      ? (texte) => (
          <TexteCite
            texte={texte}
            citations={citations}
            onOpen={onOpenCitation}
            documentActif={documentActif}
          />
        )
      : parseInline

  return (
    <div className="flex flex-col gap-2 text-sm leading-relaxed text-encre">
      {renderLines(text.replace(/\r\n/g, '\n').split('\n'), renderInline)}
    </div>
  )
}

type RenderInline = (texte: string) => ReactNode

const HEADING_RE = /^(#{1,6})\s+(.*)$/
const TABLE_SEPARATOR_RE = /^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$/
const UL_RE = /^[-*]\s+(.*)$/
const OL_RE = /^\d+\.\s+(.*)$/
const INDENTED_RE = /^\s+\S/
const HR_RE = /^-{3,}$/

const HEADING_CLASSES: Record<number, string> = {
  1: 'text-base font-semibold text-encre',
  2: 'text-base font-semibold text-encre',
  3: 'text-sm font-semibold text-encre',
  4: 'text-sm font-semibold text-encre',
  5: 'text-sm font-medium text-encre',
  6: 'text-sm font-medium text-encre-2',
}

function parseInline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith('**') && part.endsWith('**') ? (
      <strong key={i} className="font-semibold text-encre">
        {part.slice(2, -2)}
      </strong>
    ) : (
      <span key={i}>{part}</span>
    ),
  )
}

function splitTableRow(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.trim())
}

function renderLines(rawLines: string[], renderInline: RenderInline): ReactNode[] {
  const lines = rawLines.map((l) => l.replace(/\s+$/, ''))
  const out: ReactNode[] = []
  let i = 0
  let key = 0

  while (i < lines.length) {
    const line = lines[i]

    if (!line.trim()) {
      i++
      continue
    }

    const heading = HEADING_RE.exec(line)
    if (heading) {
      const level = heading[1].length
      out.push(
        createElement(`h${level}`, { key: key++, className: HEADING_CLASSES[level] }, renderInline(heading[2])),
      )
      i++
      continue
    }

    if (HR_RE.test(line.trim())) {
      out.push(<hr key={key++} className="border-bord" />)
      i++
      continue
    }

    if (line.trim().startsWith('|') && i + 1 < lines.length && TABLE_SEPARATOR_RE.test(lines[i + 1].trim())) {
      const header = splitTableRow(line)
      i += 2
      const rows: string[][] = []
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        rows.push(splitTableRow(lines[i]))
        i++
      }
      out.push(
        <table key={key++} className="w-full border-collapse text-left text-xs">
          <thead>
            <tr className="border-b border-bord-fort bg-surface-2">
              {header.map((cell, ci) => (
                <th key={ci} className="px-2 py-1 font-medium text-encre-2">
                  {renderInline(cell)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, ri) => (
              <tr key={ri} className="border-b border-surface-3">
                {row.map((cell, ci) => (
                  <td key={ci} className="px-2 py-1.5 align-top">
                    {renderInline(cell)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>,
      )
      continue
    }

    if (UL_RE.test(line) || OL_RE.test(line)) {
      const ordered = OL_RE.test(line)
      const items: ReactNode[] = []
      while (i < lines.length) {
        const marker = ordered ? OL_RE.exec(lines[i]) : UL_RE.exec(lines[i])
        if (!marker) break
        i++
        const extraLines: string[] = []
        const subBullets: string[] = []
        while (i < lines.length && INDENTED_RE.test(lines[i])) {
          const continuation = lines[i].trim()
          const subUl = UL_RE.exec(continuation)
          if (subUl) subBullets.push(subUl[1])
          else extraLines.push(continuation)
          i++
        }
        items.push(
          <li key={items.length}>
            {renderInline(marker[1])}
            {extraLines.map((l, li) => (
              <span key={`e${li}`}> {renderInline(l)}</span>
            ))}
            {subBullets.length > 0 && (
              <ul className="mt-1 list-disc pl-5">
                {subBullets.map((b, bi) => (
                  <li key={bi}>{renderInline(b)}</li>
                ))}
              </ul>
            )}
          </li>,
        )
      }
      out.push(
        createElement(
          ordered ? 'ol' : 'ul',
          { key: key++, className: ordered ? 'list-decimal pl-5' : 'list-disc pl-5' },
          items,
        ),
      )
      continue
    }

    const paraLines: string[] = []
    while (
      i < lines.length &&
      lines[i].trim() &&
      !HEADING_RE.test(lines[i]) &&
      !HR_RE.test(lines[i].trim()) &&
      !UL_RE.test(lines[i]) &&
      !OL_RE.test(lines[i]) &&
      !lines[i].trim().startsWith('|')
    ) {
      paraLines.push(lines[i].trim())
      i++
    }
    out.push(
      <p key={key++}>
        {paraLines.map((l, li) => (
          <span key={li}>
            {li > 0 && <br />}
            {renderInline(l)}
          </span>
        ))}
      </p>,
    )
  }

  return out
}
