import { useState } from 'react'
import type { ClassificationEntry, ReorgReportEntry } from '../types'

interface TreeLeaf {
  name: string
  meta?: string
}

interface TreeNode {
  name: string
  path: string
  children: Map<string, TreeNode>
  files: TreeLeaf[]
}

function createNode(name: string, path: string): TreeNode {
  return { name, path, children: new Map(), files: [] }
}

function insertPath(root: TreeNode, segments: string[], leaf: TreeLeaf) {
  let node = root
  for (const segment of segments) {
    let child = node.children.get(segment)
    if (!child) {
      child = createNode(segment, `${node.path}/${segment}`)
      node.children.set(segment, child)
    }
    node = child
  }
  node.files.push(leaf)
}

function buildTree(items: { segments: string[]; leaf: TreeLeaf }[]): TreeNode {
  const root = createNode('', '')
  for (const { segments, leaf } of items) {
    insertPath(root, segments, leaf)
  }
  return root
}

/** Dérive l'arborescence proposée depuis le plan éditable (avant application). */
export function classificationEntriesToTree(entries: ClassificationEntry[]): TreeNode {
  return buildTree(
    entries
      .filter((e): e is ClassificationEntry & { final_category: string } => Boolean(e.final_category))
      .map((e) => {
        const segments = e.final_category.split('/')
        if (e.final_lot) segments.push(`LOT ${e.final_lot}`)
        return {
          segments,
          leaf: { name: e.final_filename ?? e.filename, meta: e.is_manually_corrected ? 'corrigé' : undefined },
        }
      }),
  )
}

/** Dérive l'arborescence réellement copiée depuis le rapport (après application) — `target`
 * encode déjà le chemin organized/ complet (catégorie/lot/fichier). */
export function reorgReportEntriesToTree(entries: ReorgReportEntry[]): TreeNode {
  return buildTree(
    entries.map((e) => {
      const segments = e.target.split('/')
      const name = segments.pop() ?? e.target
      return { segments, leaf: { name } }
    }),
  )
}

function countFiles(node: TreeNode): number {
  let count = node.files.length
  for (const child of node.children.values()) count += countFiles(child)
  return count
}

/** Sérialise l'arbre en liste Markdown indentée (pour le rapport téléchargeable). */
export function treeToMarkdown(root: TreeNode, depth = 0): string {
  const lines: string[] = []
  const childFolders = [...root.children.values()].sort((a, b) => a.name.localeCompare(b.name))
  const files = [...root.files].sort((a, b) => a.name.localeCompare(b.name))
  const indent = '  '.repeat(depth)

  for (const folder of childFolders) {
    const total = countFiles(folder)
    lines.push(`${indent}- **${folder.name}/** (${total} fichier${total > 1 ? 's' : ''})`)
    lines.push(treeToMarkdown(folder, depth + 1))
  }
  for (const file of files) {
    lines.push(`${indent}- ${file.name}${file.meta ? ` _(${file.meta})_` : ''}`)
  }
  return lines.filter(Boolean).join('\n')
}

function FolderRow({
  node,
  depth,
  collapsed,
  onToggle,
  showFiles,
}: {
  node: TreeNode
  depth: number
  collapsed: Set<string>
  onToggle: (path: string) => void
  showFiles: boolean
}) {
  const expanded = !collapsed.has(node.path)
  const childFolders = [...node.children.values()].sort((a, b) => a.name.localeCompare(b.name))
  const files = [...node.files].sort((a, b) => a.name.localeCompare(b.name))
  const total = countFiles(node)

  return (
    <div>
      <button
        type="button"
        onClick={() => onToggle(node.path)}
        className="flex w-full items-center gap-2 rounded px-1.5 py-1.5 text-left text-sm hover:bg-blue-50"
        style={{ paddingLeft: `${depth * 16 + 6}px` }}
      >
        <span className={`w-3 shrink-0 text-[10px] ${expanded ? 'text-blue-500' : 'text-slate-300'}`}>
          {expanded ? '▾' : '▸'}
        </span>
        <span className="truncate font-semibold text-slate-700">{node.name}</span>
        <span className="ml-auto shrink-0 rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-500">
          {total} fichier{total > 1 ? 's' : ''}
        </span>
      </button>
      {expanded && (
        <div>
          {childFolders.map((child) => (
            <FolderRow
              key={child.path}
              node={child}
              depth={depth + 1}
              collapsed={collapsed}
              onToggle={onToggle}
              showFiles={showFiles}
            />
          ))}
          {showFiles &&
            files.map((file, i) => (
              <div
                key={i}
                className="flex items-center gap-1.5 px-1.5 py-1 text-xs text-slate-500"
                style={{ paddingLeft: `${(depth + 1) * 16 + 21}px` }}
                title={file.name}
              >
                <span className="shrink-0 text-slate-300">·</span>
                <span className="truncate">{file.name}</span>
                {file.meta && (
                  <span className="ml-1 shrink-0 rounded bg-amber-100 px-1 text-[10px] text-amber-700">
                    {file.meta}
                  </span>
                )}
              </div>
            ))}
        </div>
      )}
    </div>
  )
}

export function OrganizedTree({ root, title }: { root: TreeNode; title?: string }) {
  const topFolders = [...root.children.values()].sort((a, b) => a.name.localeCompare(b.name))
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [showFiles, setShowFiles] = useState(true)

  if (topFolders.length === 0 && root.files.length === 0) {
    return <p className="text-xs text-slate-400">Aucun fichier à afficher.</p>
  }

  const totalFiles = countFiles(root)

  const handleToggle = (path: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  }

  // Les deux modes montrent toujours l'arborescence complète (tous les dossiers, à tous les
  // niveaux) — seule la présence des fichiers change. On repart d'une arborescence entièrement
  // dépliée à chaque changement de mode, plutôt que de dépendre de l'état de pliage individuel
  // laissé par un précédent survol manuel.
  const handleSetMode = (mode: 'folded' | 'expanded') => {
    setCollapsed(new Set())
    setShowFiles(mode === 'expanded')
  }

  return (
    <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 bg-slate-50 px-3 py-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-slate-700">{title ?? 'Arborescence'}</span>
          <span className="rounded-full bg-blue-100 px-2 py-0.5 text-[11px] font-medium text-blue-700">
            {totalFiles} fichier{totalFiles > 1 ? 's' : ''}
          </span>
        </div>
        <div className="flex overflow-hidden rounded border border-slate-200">
          <button
            type="button"
            onClick={() => handleSetMode('folded')}
            aria-pressed={!showFiles}
            className={`px-2 py-1 text-[11px] font-medium ${
              !showFiles ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 hover:bg-slate-100'
            }`}
          >
            Vue pliée (dossiers)
          </button>
          <button
            type="button"
            onClick={() => handleSetMode('expanded')}
            aria-pressed={showFiles}
            className={`border-l border-slate-200 px-2 py-1 text-[11px] font-medium ${
              showFiles ? 'bg-blue-600 text-white' : 'bg-white text-slate-600 hover:bg-slate-100'
            }`}
          >
            Vue dépliée (dossiers + fichiers)
          </button>
        </div>
      </div>
      <div className="max-h-[28rem] overflow-y-auto py-1">
        {topFolders.map((node) => (
          <FolderRow
            key={node.path}
            node={node}
            depth={0}
            collapsed={collapsed}
            onToggle={handleToggle}
            showFiles={showFiles}
          />
        ))}
      </div>
    </div>
  )
}

export type { TreeNode }
