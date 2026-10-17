import { useState, useEffect } from 'react'
import { X, FolderInput, Loader2 } from 'lucide-react'
import FolderPicker from './FolderPicker'

export default function MoveDialog({
  isOpen,
  onClose,
  onMove,
  items = [], // Array of items to move (files or folders)
  isLoading = false,
}) {
  const [selectedFolder, setSelectedFolder] = useState(null)
  const [selectedFolderId, setSelectedFolderId] = useState(null)

  // Reset selection when dialog opens
  useEffect(() => {
    if (isOpen) {
      setSelectedFolder(null)
      setSelectedFolderId(null)
    }
  }, [isOpen])

  useEffect(() => {
    const handleEscape = (e) => {
      if (e.key === 'Escape' && isOpen) {
        onClose()
      }
    }
    document.addEventListener('keydown', handleEscape)
    return () => document.removeEventListener('keydown', handleEscape)
  }, [isOpen, onClose])

  const handleSelect = (folder) => {
    setSelectedFolder(folder)
    setSelectedFolderId(folder?.id || null)
  }

  const handleMove = () => {
    onMove(items, selectedFolderId)
  }

  if (!isOpen) return null

  // Get IDs to exclude (can't move folder into itself or its children)
  const excludeIds = items
    .filter(item => item.type === 'folder')
    .map(item => item.id)

  // Get current parent folder (for display)
  const currentFolderId = items[0]?.parent || items[0]?.folder || null

  // Check if destination is same as current
  const isSameLocation = selectedFolderId === currentFolderId

  // Item count text
  const itemCount = items.length
  const itemText = itemCount === 1
    ? (items[0].type === 'folder' ? `"${items[0].name}"` : `"${items[0].name}"`)
    : `${itemCount} items`

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />

      {/* Dialog */}
      <div className="relative bg-white rounded-lg shadow-xl max-w-md w-full mx-4 animate-scale-in flex flex-col max-h-[80vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b flex-shrink-0">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-blue-100 rounded-lg">
              <FolderInput className="h-5 w-5 text-blue-600" />
            </div>
            <div>
              <h3 className="text-lg font-semibold text-gray-900">
                Move {itemText}
              </h3>
              <p className="text-sm text-gray-500">Select destination folder</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded-lg hover:bg-gray-100 text-gray-400"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Content */}
        <div className="p-4 flex-1 overflow-hidden">
          <div className="border rounded-lg h-64 overflow-hidden">
            <FolderPicker
              selectedFolderId={selectedFolderId}
              onSelect={handleSelect}
              excludeIds={excludeIds}
              showRoot={true}
              className="h-full p-2"
            />
          </div>

          {selectedFolder && (
            <div className="mt-3 p-3 bg-gray-50 rounded-lg">
              <p className="text-sm text-gray-600">
                Move to: <span className="font-medium text-gray-900">{selectedFolder.name}</span>
              </p>
            </div>
          )}

          {selectedFolderId === null && (
            <div className="mt-3 p-3 bg-gray-50 rounded-lg">
              <p className="text-sm text-gray-600">
                Move to: <span className="font-medium text-gray-900">My Drive (root)</span>
              </p>
            </div>
          )}

          {isSameLocation && (
            <p className="mt-2 text-sm text-yellow-600">
              Items are already in this location
            </p>
          )}
        </div>

        {/* Footer */}
        <div className="flex gap-3 justify-end px-6 py-4 border-t flex-shrink-0">
          <button
            type="button"
            onClick={onClose}
            disabled={isLoading}
            className="px-4 py-2 text-sm font-medium text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={handleMove}
            disabled={isLoading || isSameLocation}
            className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 disabled:opacity-50 flex items-center gap-2"
          >
            {isLoading && <Loader2 className="h-4 w-4 animate-spin" />}
            {isLoading ? 'Moving...' : 'Move here'}
          </button>
        </div>
      </div>

      <style>{`
        @keyframes scale-in {
          from {
            transform: scale(0.95);
            opacity: 0;
          }
          to {
            transform: scale(1);
            opacity: 1;
          }
        }
        .animate-scale-in {
          animation: scale-in 0.15s ease-out;
        }
      `}</style>
    </div>
  )
}
