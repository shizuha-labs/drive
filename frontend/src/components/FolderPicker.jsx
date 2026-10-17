import { useState, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Folder, FolderOpen, ChevronRight, ChevronDown, Home, Loader2 } from 'lucide-react'
import { getFolders } from '../services/api'

function FolderTreeItem({
  folder,
  selectedId,
  onSelect,
  expandedIds,
  onToggleExpand,
  excludeIds = [],
  level = 0
}) {
  const isSelected = selectedId === folder.id
  const isExpanded = expandedIds.has(folder.id)
  const isExcluded = excludeIds.includes(folder.id)
  const hasChildren = folder.children && folder.children.length > 0

  if (isExcluded) return null

  return (
    <div>
      <div
        className={`flex items-center gap-1 py-1.5 px-2 rounded-lg cursor-pointer transition-colors ${
          isSelected
            ? 'bg-blue-100 text-blue-700'
            : 'hover:bg-gray-100 text-gray-700'
        }`}
        style={{ paddingLeft: `${level * 16 + 8}px` }}
        onClick={() => !isExcluded && onSelect(folder)}
      >
        {hasChildren ? (
          <button
            onClick={(e) => {
              e.stopPropagation()
              onToggleExpand(folder.id)
            }}
            className="p-0.5 hover:bg-gray-200 rounded"
          >
            {isExpanded ? (
              <ChevronDown className="h-4 w-4 text-gray-400" />
            ) : (
              <ChevronRight className="h-4 w-4 text-gray-400" />
            )}
          </button>
        ) : (
          <span className="w-5" />
        )}
        {isExpanded ? (
          <FolderOpen className="h-5 w-5 text-yellow-500 flex-shrink-0" />
        ) : (
          <Folder className="h-5 w-5 text-yellow-500 flex-shrink-0" />
        )}
        <span className="truncate text-sm">{folder.name}</span>
      </div>

      {hasChildren && isExpanded && (
        <div>
          {folder.children
            .filter(child => !excludeIds.includes(child.id))
            .map((child) => (
              <FolderTreeItem
                key={child.id}
                folder={child}
                selectedId={selectedId}
                onSelect={onSelect}
                expandedIds={expandedIds}
                onToggleExpand={onToggleExpand}
                excludeIds={excludeIds}
                level={level + 1}
              />
            ))}
        </div>
      )}
    </div>
  )
}

export default function FolderPicker({
  selectedFolderId,
  onSelect,
  excludeIds = [],
  showRoot = true,
  className = ''
}) {
  const [expandedIds, setExpandedIds] = useState(new Set())

  const { data: folders, isLoading, error } = useQuery({
    queryKey: ['folders', 'tree'],
    queryFn: async () => {
      const response = await getFolders()
      return buildTree(response)
    },
  })

  // Build folder tree from flat list
  const buildTree = (flatFolders) => {
    const folderMap = new Map()
    const rootFolders = []

    // First pass: create map
    flatFolders.forEach(folder => {
      folderMap.set(folder.id, { ...folder, children: [] })
    })

    // Second pass: build tree
    flatFolders.forEach(folder => {
      const node = folderMap.get(folder.id)
      if (folder.parent) {
        const parent = folderMap.get(folder.parent)
        if (parent) {
          parent.children.push(node)
        } else {
          rootFolders.push(node)
        }
      } else {
        rootFolders.push(node)
      }
    })

    // Sort children by name
    const sortChildren = (nodes) => {
      nodes.sort((a, b) => a.name.localeCompare(b.name))
      nodes.forEach(node => sortChildren(node.children))
    }
    sortChildren(rootFolders)

    return rootFolders
  }

  const handleToggleExpand = (folderId) => {
    setExpandedIds(prev => {
      const newSet = new Set(prev)
      if (newSet.has(folderId)) {
        newSet.delete(folderId)
      } else {
        newSet.add(folderId)
      }
      return newSet
    })
  }

  const handleSelect = (folder) => {
    onSelect(folder)
  }

  const handleSelectRoot = () => {
    onSelect(null) // null means root/My Drive
  }

  // Auto-expand parent folders when a folder is selected
  useEffect(() => {
    if (selectedFolderId && folders) {
      const findPath = (nodes, targetId, path = []) => {
        for (const node of nodes) {
          if (node.id === targetId) {
            return path
          }
          if (node.children.length > 0) {
            const found = findPath(node.children, targetId, [...path, node.id])
            if (found) return found
          }
        }
        return null
      }

      const path = findPath(folders, selectedFolderId)
      if (path) {
        setExpandedIds(prev => new Set([...prev, ...path]))
      }
    }
  }, [selectedFolderId, folders])

  if (isLoading) {
    return (
      <div className={`flex items-center justify-center py-8 ${className}`}>
        <Loader2 className="h-6 w-6 animate-spin text-gray-400" />
      </div>
    )
  }

  if (error) {
    return (
      <div className={`text-center py-8 text-red-500 ${className}`}>
        Failed to load folders
      </div>
    )
  }

  return (
    <div className={`overflow-auto ${className}`}>
      {showRoot && (
        <div
          className={`flex items-center gap-2 py-1.5 px-2 rounded-lg cursor-pointer transition-colors ${
            selectedFolderId === null
              ? 'bg-blue-100 text-blue-700'
              : 'hover:bg-gray-100 text-gray-700'
          }`}
          onClick={handleSelectRoot}
        >
          <Home className="h-5 w-5 text-blue-500 flex-shrink-0" />
          <span className="text-sm font-medium">My Drive</span>
        </div>
      )}

      {folders && folders.length > 0 ? (
        folders.map((folder) => (
          <FolderTreeItem
            key={folder.id}
            folder={folder}
            selectedId={selectedFolderId}
            onSelect={handleSelect}
            expandedIds={expandedIds}
            onToggleExpand={handleToggleExpand}
            excludeIds={excludeIds}
            level={showRoot ? 1 : 0}
          />
        ))
      ) : (
        <div className="text-center py-4 text-gray-500 text-sm">
          No folders found
        </div>
      )}
    </div>
  )
}
