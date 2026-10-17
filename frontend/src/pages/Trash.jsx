import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Trash2, File, FileText, Image, Film, Music, Archive, Code, RotateCcw, X, Folder } from 'lucide-react'
import { getTrashedFiles, restoreFromTrash, deleteForever, restoreFolderFromTrash } from '../services/api'
import LoadingSpinner from '../components/LoadingSpinner'
import { formatDistanceToNow } from 'date-fns'
import { useState } from 'react'

const fileTypeIcons = {
  document: FileText,
  spreadsheet: FileText,
  presentation: FileText,
  image: Image,
  video: Film,
  audio: Music,
  archive: Archive,
  code: Code,
  other: File,
}

export default function Trash() {
  const queryClient = useQueryClient()
  const [selectedFiles, setSelectedFiles] = useState([])

  // DRV-75: GET /trash/ returns { files: [...], folders: [...] } — an OBJECT.
  // This previously bound the envelope straight to `files`, so `files.length`
  // was undefined, `undefined > 0` was false, and the page rendered
  // "Trash is empty" unconditionally — hiding files AND folders that the API
  // was returning correctly. The data was never lost; it was never displayed.
  const { data, isLoading } = useQuery({
    queryKey: ['trash'],
    queryFn: getTrashedFiles,
  })
  const files = data?.files ?? []
  const folders = data?.folders ?? []
  const isEmpty = files.length === 0 && folders.length === 0

  // DRV-75 P1 (revi): a trashed child whose parent is ALSO trashed must not
  // expose its own Restore — restoring it alone leaves it under a still-trashed
  // parent, so it is not browser-reachable (orphaned in the Drive tree). Restore
  // only on subtree roots: a trashed folder whose parent is not itself in trash
  // (parent === null, or the parent folder is not in the trashed set). Restoring
  // a root brings its still-trashed descendants back with it. Child rows stay
  // visible (so the user sees the whole subtree) but show a "restores with parent"
  // hint instead of a Restore control.
  const trashedFolderIds = new Set(folders.map((f) => f.id))
  const isSubtreeRoot = (folder) => !folder.parent || !trashedFolderIds.has(folder.parent)

  const restoreMutation = useMutation({
    mutationFn: restoreFromTrash,
    onSuccess: () => {
      queryClient.invalidateQueries(['trash'])
      queryClient.invalidateQueries(['files'])
      setSelectedFiles([])
    },
  })

  const deleteMutation = useMutation({
    mutationFn: deleteForever,
    onSuccess: () => {
      queryClient.invalidateQueries(['trash'])
      setSelectedFiles([])
    },
  })

  const restoreFolderMutation = useMutation({
    mutationFn: restoreFolderFromTrash,
    onSuccess: () => {
      queryClient.invalidateQueries(['trash'])
      queryClient.invalidateQueries(['folders'])
      queryClient.invalidateQueries(['files'])
      // DRV-93: restored folders must reappear in the sidebar tree too.
      queryClient.invalidateQueries(['root-folder-children'])
      queryClient.invalidateQueries(['folder-children'])
    },
  })

  const toggleSelect = (fileId) => {
    setSelectedFiles((prev) =>
      prev.includes(fileId)
        ? prev.filter((id) => id !== fileId)
        : [...prev, fileId]
    )
  }

  const handleRestoreSelected = () => {
    selectedFiles.forEach((id) => restoreMutation.mutate(id))
  }

  const handleDeleteSelected = () => {
    if (window.confirm('Permanently delete selected files? This cannot be undone.')) {
      selectedFiles.forEach((id) => deleteMutation.mutate(id))
    }
  }

  if (isLoading) {
    return (
      <div className="flex justify-center items-center h-64">
        <LoadingSpinner size="lg" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-gray-900 flex items-center">
          <Trash2 className="mr-2 h-6 w-6 text-gray-500" />
          Trash
        </h1>
        {selectedFiles.length > 0 && (
          <div className="flex space-x-2">
            <button
              onClick={handleRestoreSelected}
              className="px-3 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 flex items-center"
            >
              <RotateCcw className="h-4 w-4 mr-1" />
              Restore ({selectedFiles.length})
            </button>
            <button
              onClick={handleDeleteSelected}
              className="px-3 py-2 text-sm bg-red-600 text-white rounded-lg hover:bg-red-700 flex items-center"
            >
              <X className="h-4 w-4 mr-1" />
              Delete Forever ({selectedFiles.length})
            </button>
          </div>
        )}
      </div>

      <p className="text-sm text-gray-500">
        Files in trash are automatically deleted after 30 days
      </p>

      <div className="bg-white rounded-lg shadow-sm border border-gray-200" data-testid="trash-list">
        {!isEmpty ? (
          <div className="divide-y divide-gray-200">
            {folders.map((folder) => (
              <div
                key={folder.id}
                className="flex items-center justify-between p-4 hover:bg-gray-50"
              >
                <div className="flex items-center space-x-3">
                  <div className="h-4 w-4" />
                  <Folder className="h-10 w-10 text-gray-400" />
                  <div>
                    <div className="text-sm font-medium text-gray-900">
                      {folder.name}
                    </div>
                    <div className="text-xs text-gray-500">
                      Folder
                      {folder.trashed_at
                        ? ` · Deleted ${formatDistanceToNow(new Date(folder.trashed_at), { addSuffix: true })}`
                        : ''}
                      {typeof folder.days_until_purge === 'number'
                        ? ` · ${folder.days_until_purge}d until purge`
                        : ''}
                    </div>
                  </div>
                </div>
                <div className="flex space-x-2">
                  {isSubtreeRoot(folder) ? (
                    <button
                      onClick={() => restoreFolderMutation.mutate(folder.id)}
                      className="p-2 rounded-full hover:bg-gray-100"
                      title="Restore"
                      data-testid="folder-restore"
                    >
                      <RotateCcw className="h-5 w-5 text-gray-500" />
                    </button>
                  ) : (
                    <span
                      className="text-xs text-gray-400 italic self-center"
                      data-testid="folder-restore-with-parent"
                      title="This folder will be restored together with its parent"
                    >
                      Restores with parent
                    </span>
                  )}
                </div>
              </div>
            ))}
            {files.map((file) => {
              const Icon = fileTypeIcons[file.file_type] || File
              const isSelected = selectedFiles.includes(file.id)
              return (
                <div
                  key={file.id}
                  className={`flex items-center justify-between p-4 hover:bg-gray-50 ${
                    isSelected ? 'bg-blue-50' : ''
                  }`}
                >
                  <div className="flex items-center space-x-3">
                    <input
                      type="checkbox"
                      checked={isSelected}
                      onChange={() => toggleSelect(file.id)}
                      className="h-4 w-4 text-blue-600 rounded border-gray-300"
                    />
                    {file.has_thumbnail && file.thumbnail_url ? (
                      <img
                        src={file.thumbnail_url}
                        alt={file.name}
                        className="h-10 w-10 object-cover rounded"
                      />
                    ) : (
                      <Icon className="h-10 w-10 text-gray-400" />
                    )}
                    <div>
                      <div className="text-sm font-medium text-gray-900">
                        {file.name}
                      </div>
                      <div className="text-xs text-gray-500">
                        {file.size_human} · Deleted{' '}
                        {formatDistanceToNow(new Date(file.deleted_at), {
                          addSuffix: true,
                        })}
                      </div>
                    </div>
                  </div>
                  <div className="flex space-x-2">
                    <button
                      onClick={() => restoreMutation.mutate(file.id)}
                      className="p-2 rounded-full hover:bg-gray-100"
                      title="Restore"
                    >
                      <RotateCcw className="h-5 w-5 text-gray-500" />
                    </button>
                    <button
                      onClick={() => {
                        if (window.confirm('Permanently delete this file?')) {
                          deleteMutation.mutate(file.id)
                        }
                      }}
                      className="p-2 rounded-full hover:bg-gray-100"
                      title="Delete forever"
                    >
                      <X className="h-5 w-5 text-red-500" />
                    </button>
                  </div>
                </div>
              )
            })}
          </div>
        ) : (
          <div className="text-center py-16">
            <Trash2 className="h-16 w-16 text-gray-300 mx-auto mb-4" />
            <h3 className="text-lg font-medium text-gray-900 mb-2">
              Trash is empty
            </h3>
            <p className="text-gray-500">
              Files and folders you delete will appear here
            </p>
          </div>
        )}
      </div>
    </div>
  )
}
