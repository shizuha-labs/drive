import { useState, useEffect, useRef } from 'react'
import { X, Pencil, Folder, FileText } from 'lucide-react'

export default function RenameDialog({
  isOpen,
  onClose,
  onRename,
  item,
  itemType = 'file', // 'file' | 'folder'
  isLoading = false,
}) {
  const [name, setName] = useState('')
  const [error, setError] = useState('')
  const inputRef = useRef(null)

  useEffect(() => {
    if (isOpen && item) {
      setName(item.name)
      setError('')
      // Focus and select text (without extension for files)
      setTimeout(() => {
        if (inputRef.current) {
          inputRef.current.focus()
          if (itemType === 'file' && item.extension) {
            // Select just the filename, not the extension
            const dotIndex = item.name.lastIndexOf('.')
            if (dotIndex > 0) {
              inputRef.current.setSelectionRange(0, dotIndex)
            } else {
              inputRef.current.select()
            }
          } else {
            inputRef.current.select()
          }
        }
      }, 50)
    }
  }, [isOpen, item, itemType])

  useEffect(() => {
    const handleEscape = (e) => {
      if (e.key === 'Escape' && isOpen) {
        onClose()
      }
    }
    document.addEventListener('keydown', handleEscape)
    return () => document.removeEventListener('keydown', handleEscape)
  }, [isOpen, onClose])

  const handleSubmit = (e) => {
    e.preventDefault()

    const trimmedName = name.trim()

    if (!trimmedName) {
      setError('Name cannot be empty')
      return
    }

    if (trimmedName === item.name) {
      onClose()
      return
    }

    // Basic validation for invalid characters
    if (/[<>:"/\\|?*]/.test(trimmedName)) {
      setError('Name contains invalid characters')
      return
    }

    onRename(item, trimmedName)
  }

  if (!isOpen) return null

  const Icon = itemType === 'folder' ? Folder : FileText

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />

      {/* Dialog */}
      <div className="relative bg-white rounded-lg shadow-xl max-w-md w-full mx-4 animate-scale-in">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-blue-100 rounded-lg">
              <Pencil className="h-5 w-5 text-blue-600" />
            </div>
            <h3 className="text-lg font-semibold text-gray-900">
              Rename {itemType}
            </h3>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded-lg hover:bg-gray-100 text-gray-400"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Content */}
        <form onSubmit={handleSubmit} className="p-6">
          {/* Current item info */}
          <div className="flex items-center gap-3 p-3 bg-gray-50 rounded-lg mb-4">
            <Icon className="h-8 w-8 text-gray-400" />
            <div className="text-sm text-gray-600 truncate">{item?.name}</div>
          </div>

          {/* Input */}
          <div className="mb-4">
            <label className="block text-sm font-medium text-gray-700 mb-1">
              New name
            </label>
            <input
              ref={inputRef}
              type="text"
              value={name}
              onChange={(e) => {
                setName(e.target.value)
                setError('')
              }}
              className={`w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent ${
                error ? 'border-red-500' : 'border-gray-300'
              }`}
              placeholder={`Enter ${itemType} name`}
            />
            {error && <p className="mt-1 text-sm text-red-600">{error}</p>}
          </div>

          {/* Buttons */}
          <div className="flex gap-3 justify-end">
            <button
              type="button"
              onClick={onClose}
              disabled={isLoading}
              className="px-4 py-2 text-sm font-medium text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isLoading || !name.trim()}
              className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 disabled:opacity-50"
            >
              {isLoading ? 'Renaming...' : 'Rename'}
            </button>
          </div>
        </form>
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
