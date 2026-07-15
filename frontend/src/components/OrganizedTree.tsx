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

function FolderRow({ node, depth }: { node: TreeNode; depth: number }) {
  const [expanded, setExpanded] = useState(true)
  const childFolders = [...node.children.values()].sort((a, b) => a.name.localeCompare(b.name))
  const files = [...node.files].sort((a, b) => a.name.localeCompare(b.name))
  const total = countFiles(node)

  return (
    <div>
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-left text-xs hover:bg-slate-100"
        style={{ paddingLeft: `${depth * 14 + 6}px` }}
      >
        <span className="text-slate-400">{expanded ? '▾' : '▸'}</span>
        <span className="font-medium text-slate-700">{node.name}</span>
        <span className="ml-auto shrink-0 text-slate-400">
          {total} fichier{total > 1 ? 's' : ''}
        </span>
      </button>
      {expanded && (
        <div>
          {childFolders.map((child) => (
            <FolderRow key={child.path} node={child} depth={depth + 1} />
          ))}
          {files.map((file, i) => (
            <div
              key={i}
              className="flex items-center gap-1.5 px-1.5 py-0.5 text-xs text-slate-500"
              style={{ paddingLeft: `${(depth + 1) * 14 + 6}px` }}
              title={file.name}
            >
              <span className="shrink-0 text-slate-300">·</span>
              <span className="truncate">{file.name}</span>
              {file.meta && (
                <span className="ml-1 shrink-0 rounded bg-slate-100 px-1 text-[10px] text-slate-500">
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

export function OrganizedTree({ root }: { root: TreeNode }) {
  const topFolders = [...root.children.values()].sort((a, b) => a.name.localeCompare(b.name))
  if (topFolders.length === 0 && root.files.length === 0) {
    return <p className="text-xs text-slate-400">Aucun fichier à afficher.</p>
  }
  return (
    <div className="max-h-96 overflow-y-auto rounded-lg border border-slate-200 bg-white py-1">
      {topFolders.map((node) => (
        <FolderRow key={node.path} node={node} depth={0} />
      ))}
    </div>
  )
}
