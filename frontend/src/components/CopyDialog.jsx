import { useState, useEffect } from 'react'
import { X, Copy, Loader2 } from 'lucide-react'
import FolderPicker from './FolderPicker'

export default function CopyDialog({
  isOpen,
  onClose,
  onCopy,
  items = [], // Array of items to copy (files only - folders can't be copied)
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

  const handleCopy = () => {
    onCopy(items, selectedFolderId)
  }

  if (!isOpen) return null

  // Item count text
  const itemCount = items.length
  const itemText = itemCount === 1
    ? `"${items[0].name}"`
    : `${itemCount} files`

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />

      {/* Dialog */}
      <div className="relative bg-white rounded-lg shadow-xl max-w-md w-full mx-4 animate-scale-in flex flex-col max-h-[80vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b flex-shrink-0">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-green-100 rounded-lg">
              <Copy className="h-5 w-5 text-green-600" />
            </div>
            <div>
              <h3 className="text-lg font-semibold text-gray-900">
                Copy {itemText}
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
              showRoot={true}
              className="h-full p-2"
            />
          </div>

          {selectedFolder && (
            <div className="mt-3 p-3 bg-gray-50 rounded-lg">
              <p className="text-sm text-gray-600">
                Copy to: <span className="font-medium text-gray-900">{selectedFolder.name}</span>
              </p>
            </div>
          )}

          {selectedFolderId === null && (
            <div className="mt-3 p-3 bg-gray-50 rounded-lg">
              <p className="text-sm text-gray-600">
                Copy to: <span className="font-medium text-gray-900">My Drive (root)</span>
              </p>
            </div>
          )}

          <p className="mt-3 text-xs text-gray-500">
            A copy will be created with " - Copy" added to the filename.
          </p>
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
            onClick={handleCopy}
            disabled={isLoading}
            className="px-4 py-2 text-sm font-medium text-white bg-green-600 rounded-lg hover:bg-green-700 disabled:opacity-50 flex items-center gap-2"
          >
            {isLoading && <Loader2 className="h-4 w-4 animate-spin" />}
            {isLoading ? 'Copying...' : 'Copy here'}
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
