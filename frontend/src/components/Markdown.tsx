import type { ReactNode } from 'react'

interface Props {
  text: string
}

/** Rendu Markdown minimal, sans dépendance externe — couvre uniquement le sous-ensemble produit
 * par la synthèse projet IA (titres `#`/`##`/`###`, tableaux GFM, listes à puces, gras `**...**`,
 * paragraphes) : pas un parseur CommonMark généraliste. */
export function Markdown({ text }: Props) {
  const blocks = text.split(/\n{2,}/)

  return (
    <div className="flex flex-col gap-3 text-sm leading-relaxed text-slate-700">
      {blocks.map((block, i) => renderBlock(block, i))}
    </div>
  )
}

function parseInline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith('**') && part.endsWith('**') ? (
      <strong key={i} className="font-semibold text-slate-800">
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

function renderBlock(block: string, key: number): ReactNode {
  const trimmed = block.trim()
  if (!trimmed) return null
  const lines = trimmed
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean)

  const headingMatch = lines.length === 1 ? /^(#{1,4})\s+(.*)$/.exec(lines[0]) : null
  if (headingMatch) {
    const level = Math.min(headingMatch[1].length + 1, 6)
    const className = 'font-semibold text-slate-800'
    const content = parseInline(headingMatch[2])
    switch (level) {
      case 2:
        return (
          <h2 key={key} className={className}>
            {content}
          </h2>
        )
      case 3:
        return (
          <h3 key={key} className={className}>
            {content}
          </h3>
        )
      default:
        return (
          <h4 key={key} className={className}>
            {content}
          </h4>
        )
    }
  }

  if (lines.length >= 2 && lines.every((l) => l.startsWith('|'))) {
    const header = splitTableRow(lines[0])
    const rows = lines.slice(2).map(splitTableRow)
    return (
      <table key={key} className="w-full border-collapse text-left text-xs">
        <thead>
          <tr className="border-b border-slate-300 bg-slate-50">
            {header.map((cell, ci) => (
              <th key={ci} className="px-2 py-1 font-medium text-slate-600">
                {parseInline(cell)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => (
            <tr key={ri} className="border-b border-slate-100">
              {row.map((cell, ci) => (
                <td key={ci} className="px-2 py-1.5 align-top">
                  {parseInline(cell)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    )
  }

  if (lines.every((l) => /^[-*]\s+/.test(l))) {
    return (
      <ul key={key} className="list-disc pl-5">
        {lines.map((l, li) => (
          <li key={li}>{parseInline(l.replace(/^[-*]\s+/, ''))}</li>
        ))}
      </ul>
    )
  }

  return (
    <p key={key}>
      {lines.map((l, li) => (
        <span key={li}>
          {li > 0 && <br />}
          {parseInline(l)}
        </span>
      ))}
    </p>
  )
}
