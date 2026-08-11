import { useState } from 'react'
import type { ClassificationEntry, ReorgReportEntry } from '../types'

interface TreeLeaf {
  name: string
  meta?: string
  documentId?: string
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
      return { segments, leaf: { name, documentId: e.document_id } }
    }),
  )
}

function countFiles(node: TreeNode): number {
  let count = node.files.length
  for (const child of node.children.values()) count += countFiles(child)
  return count
}

function collectDocumentIds(node: TreeNode): string[] {
  const ids: string[] = []
  for (const file of node.files) if (file.documentId) ids.push(file.documentId)
  for (const child of node.children.values()) ids.push(...collectDocumentIds(child))
  return ids
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

/** Comme `treeToMarkdown`, mais ne liste jamais les fichiers (seulement les dossiers, à tous
 * les niveaux, avec leur nombre de pièces) — pour le rapport téléchargeable, où un DCE de
 * plusieurs centaines de fichiers rendrait l'arborescence illisible si chacun y était listé
 * individuellement (§ExtractionSheet.tsx `handleDownloadReport`). */
export function treeToMarkdownFoldersOnly(root: TreeNode, depth = 0): string {
  const lines: string[] = []
  const childFolders = [...root.children.values()].sort((a, b) => a.name.localeCompare(b.name))
  const indent = '  '.repeat(depth)

  for (const folder of childFolders) {
    const total = countFiles(folder)
    lines.push(`${indent}- **${folder.name}/** (${total} fichier${total > 1 ? 's' : ''})`)
    lines.push(treeToMarkdownFoldersOnly(folder, depth + 1))
  }
  return lines.filter(Boolean).join('\n')
}

interface SelectionProps {
  selectable?: boolean
  selected?: Set<string>
  onToggleFile?: (documentId: string) => void
  onToggleFolder?: (documentIds: string[], checked: boolean) => void
}

function TreeConnector() {
  return <span aria-hidden="true" className="absolute -left-4 top-1/2 h-px w-4 -translate-y-1/2 bg-bord-fort" />
}

function FolderRow({
  node,
  isRoot,
  collapsed,
  onToggle,
  showFiles,
  selectable,
  selected,
  onToggleFile,
  onToggleFolder,
}: {
  node: TreeNode
  isRoot?: boolean
  collapsed: Set<string>
  onToggle: (path: string) => void
  showFiles: boolean
} & SelectionProps) {
  const expanded = !collapsed.has(node.path)
  const childFolders = [...node.children.values()].sort((a, b) => a.name.localeCompare(b.name))
  const files = [...node.files].sort((a, b) => a.name.localeCompare(b.name))
  const total = countFiles(node)
  const hasVisibleChildren = childFolders.length > 0 || (showFiles && files.length > 0)

  const folderDocumentIds = selectable ? collectDocumentIds(node) : []
  const folderSelectedCount = selectable
    ? folderDocumentIds.filter((id) => selected?.has(id)).length
    : 0
  const folderAllSelected = selectable && folderDocumentIds.length > 0 && folderSelectedCount === folderDocumentIds.length

  return (
    <div>
      <div className="relative flex w-full items-center gap-2 rounded px-1.5 py-1.5 hover:bg-ardoise-clair">
        {!isRoot && <TreeConnector />}
        {selectable && folderDocumentIds.length > 0 && (
          <input
            type="checkbox"
            checked={folderAllSelected}
            ref={(el) => {
              if (el) el.indeterminate = folderSelectedCount > 0 && !folderAllSelected
            }}
            onChange={(e) => onToggleFolder?.(folderDocumentIds, e.target.checked)}
            className="shrink-0"
            title="Sélectionner tous les fichiers de ce dossier"
          />
        )}
        <button
          type="button"
          onClick={() => onToggle(node.path)}
          className="flex flex-1 items-center gap-2 text-left text-sm"
        >
          <span className={`w-3 shrink-0 text-[10px] ${expanded ? 'text-ardoise' : 'text-bord-fort'}`}>
            {expanded ? '▾' : '▸'}
          </span>
          <span className="truncate font-semibold text-encre">{node.name}</span>
          <span className="ml-auto shrink-0 rounded-full bg-surface-3 px-2 py-0.5 text-[10px] font-medium text-encre-2">
            {selectable && folderDocumentIds.length > 0 ? `${folderSelectedCount}/${total} sélectionné(s)` : `${total} fichier${total > 1 ? 's' : ''}`}
          </span>
        </button>
      </div>
      {expanded && hasVisibleChildren && (
        <div className="ml-2 border-l border-bord pl-4">
          {childFolders.map((child) => (
            <FolderRow
              key={child.path}
              node={child}
              collapsed={collapsed}
              onToggle={onToggle}
              showFiles={showFiles}
              selectable={selectable}
              selected={selected}
              onToggleFile={onToggleFile}
              onToggleFolder={onToggleFolder}
            />
          ))}
          {showFiles &&
            files.map((file, i) => (
              <div
                key={i}
                className="relative flex items-center gap-1.5 px-1.5 py-1 text-xs text-encre-2"
                title={file.name}
              >
                <TreeConnector />
                {selectable && file.documentId ? (
                  <input
                    type="checkbox"
                    checked={selected?.has(file.documentId) ?? false}
                    onChange={() => file.documentId && onToggleFile?.(file.documentId)}
                    className="shrink-0"
                  />
                ) : (
                  <span className="shrink-0 text-bord-fort">·</span>
                )}
                <span className="truncate">{file.name}</span>
                {file.meta && (
                  <span className="ml-1 shrink-0 rounded bg-ambre-clair px-1 text-[10px] text-ambre">
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

export function OrganizedTree({
  root,
  title,
  selectable,
  selected,
  onToggleFile,
  onToggleFolder,
}: { root: TreeNode; title?: string } & SelectionProps) {
  const topFolders = [...root.children.values()].sort((a, b) => a.name.localeCompare(b.name))
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [showFiles, setShowFiles] = useState(true)

  if (topFolders.length === 0 && root.files.length === 0) {
    return <p className="text-xs text-encre-3">Aucun fichier à afficher.</p>
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

  // En mode sélectionnable, les fichiers doivent toujours être visibles (sinon rien à cocher) —
  // le bascule plié/déplié reste disponible pour les dossiers, mais force showFiles=true.
  const effectiveShowFiles = selectable ? true : showFiles

  return (
    <div className="overflow-hidden rounded-lg border border-bord bg-white shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-bord bg-surface-2 px-3 py-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-encre">{title ?? 'Arborescence'}</span>
          <span className="rounded-full bg-ardoise-clair px-2 py-0.5 text-[11px] font-medium text-ardoise-fonce">
            {totalFiles} fichier{totalFiles > 1 ? 's' : ''}
          </span>
        </div>
        {!selectable && (
          <div className="flex overflow-hidden rounded border border-bord">
            <button
              type="button"
              onClick={() => handleSetMode('folded')}
              aria-pressed={!showFiles}
              className={`px-2 py-1 text-[11px] font-medium ${
                !showFiles ? 'bg-ardoise text-white' : 'bg-white text-encre-2 hover:bg-surface-3'
              }`}
            >
              Vue pliée (dossiers)
            </button>
            <button
              type="button"
              onClick={() => handleSetMode('expanded')}
              aria-pressed={showFiles}
              className={`border-l border-bord px-2 py-1 text-[11px] font-medium ${
                showFiles ? 'bg-ardoise text-white' : 'bg-white text-encre-2 hover:bg-surface-3'
              }`}
            >
              Vue dépliée (dossiers + fichiers)
            </button>
          </div>
        )}
      </div>
      <div className="max-h-[28rem] overflow-y-auto py-1">
        {topFolders.map((node) => (
          <FolderRow
            key={node.path}
            node={node}
            isRoot
            collapsed={collapsed}
            onToggle={handleToggle}
            showFiles={effectiveShowFiles}
            selectable={selectable}
            selected={selected}
            onToggleFile={onToggleFile}
            onToggleFolder={onToggleFolder}
          />
        ))}
      </div>
    </div>
  )
}

export { collectDocumentIds }
export type { TreeNode }
