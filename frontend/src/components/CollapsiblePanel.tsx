import { useState, type ReactNode } from 'react'

interface Props {
  title: string
  subtitle?: string
  defaultCollapsed?: boolean
  children: ReactNode
}

/** Volet repliable générique — contenu inchangé, juste replié par défaut pour ne pas
 * alourdir la page (ex. tableaux détaillés source→cible, inventaire complet). */
export function CollapsiblePanel({ title, subtitle, defaultCollapsed = true, children }: Props) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed)

  return (
    <div className="rounded-lg border border-bord bg-white">
      <button
        type="button"
        onClick={() => setCollapsed((v) => !v)}
        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left"
      >
        <span className="flex items-center gap-2 text-sm font-medium text-encre-2">
          <span
            className={`inline-block text-[10px] text-encre-3 transition-transform ${collapsed ? '' : 'rotate-90'}`}
          >
            ▸
          </span>
          {title}
        </span>
        {subtitle && <span className="text-xs text-encre-3">{subtitle}</span>}
      </button>
      {!collapsed && <div className="border-t border-bord">{children}</div>}
    </div>
  )
}
