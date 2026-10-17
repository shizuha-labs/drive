import { useState, useCallback } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'
import { Folder, ChevronRight, ChevronDown, Loader2 } from 'lucide-react'
import { getFolderChildren, getRootFolderChildren } from '../services/api'

function FolderTreeNode({ folder, depth = 0, currentFolderId, onNavigate }) {
  const [expanded, setExpanded] = useState(false)
  const isActive = folder.id === currentFolderId

  const { data: children, isLoading } = useQuery({
    queryKey: ['folder-children', folder.id],
    queryFn: () => getFolderChildren(folder.id),
    enabled: expanded,
    staleTime: 30_000,
  })

  const handleToggle = useCallback((e) => {
    e.stopPropagation()
    setExpanded((prev) => !prev)
  }, [])

  const handleClick = useCallback(() => {
    onNavigate(folder.id)
  }, [folder.id, onNavigate])

  return (
    <div>
      <button
        onClick={handleClick}
        className={`w-full flex items-center gap-1 px-2 py-1.5 text-sm rounded-md transition-colors text-left ${
          isActive
            ? 'bg-blue-50 text-blue-700 font-medium'
            : 'text-gray-600 hover:bg-gray-100'
        }`}
        style={{ paddingLeft: `${8 + depth * 16}px` }}
      >
        <span
          onClick={handleToggle}
          className="flex-shrink-0 w-4 h-4 flex items-center justify-center text-gray-400 hover:text-gray-600"
        >
          {isLoading ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : children && children.length > 0 ? (
            expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />
          ) : (
            <span className="w-3" />
          )}
        </span>
        <Folder className="h-4 w-4 flex-shrink-0 text-yellow-500" fill="currentColor" />
        <span className="truncate">{folder.name}</span>
      </button>
      {expanded && children && children.length > 0 && (
        <div>
          {children.map((child) => (
            <FolderTreeNode
              key={child.id}
              folder={child}
              depth={depth + 1}
              currentFolderId={currentFolderId}
              onNavigate={onNavigate}
            />
          ))}
        </div>
      )}
    </div>
  )
}

export default function FolderTreeSidebar() {
  const navigate = useNavigate()
  const { folderId } = useParams()

  const { data: rootFolders, isLoading } = useQuery({
    queryKey: ['root-folder-children'],
    queryFn: getRootFolderChildren,
    staleTime: 30_000,
  })

  const handleNavigate = useCallback(
    (id) => {
      navigate(`/my-drive/${id}`)
    },
    [navigate]
  )

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-4">
        <Loader2 className="h-4 w-4 text-gray-400 animate-spin" />
      </div>
    )
  }

  if (!rootFolders || rootFolders.length === 0) {
    return null
  }

  return (
    <div className="py-2">
      <div className="px-3 py-1 text-xs font-medium text-gray-500 uppercase tracking-wider">
        Folders
      </div>
      <div className="mt-1 space-y-0.5">
        {rootFolders.map((folder) => (
          <FolderTreeNode
            key={folder.id}
            folder={folder}
            depth={0}
            currentFolderId={folderId}
            onNavigate={handleNavigate}
          />
        ))}
      </div>
    </div>
  )
}
