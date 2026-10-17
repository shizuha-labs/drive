import { useState, useRef, useEffect } from 'react'
import {
  MoreVertical,
  FolderOpen,
  Download,
  Pencil,
  FolderInput,
  Copy,
  Star,
  StarOff,
  Share2,
  Trash2,
  Eye,
  RotateCcw,
} from 'lucide-react'

export default function ActionDropdown({
  item,
  itemType = 'file', // 'file' | 'folder' | 'trash'
  onAction,
  className = '',
}) {
  const [isOpen, setIsOpen] = useState(false)
  const dropdownRef = useRef(null)
  const buttonRef = useRef(null)

  useEffect(() => {
    const handleClickOutside = (e) => {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(e.target) &&
        buttonRef.current &&
        !buttonRef.current.contains(e.target)
      ) {
        setIsOpen(false)
      }
    }

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside)
      return () => document.removeEventListener('mousedown', handleClickOutside)
    }
  }, [isOpen])

  const handleAction = (action) => {
    onAction(action, item)
    setIsOpen(false)
  }

  const fileActions = [
    { id: 'preview', label: 'Preview', icon: Eye },
    { id: 'download', label: 'Download', icon: Download },
    { type: 'divider' },
    { id: 'rename', label: 'Rename', icon: Pencil },
    { id: 'move', label: 'Move to...', icon: FolderInput },
    { id: 'copy', label: 'Make a copy', icon: Copy },
    { type: 'divider' },
    {
      id: 'star',
      label: item?.is_starred ? 'Remove from starred' : 'Add to starred',
      icon: item?.is_starred ? StarOff : Star,
    },
    { id: 'share', label: 'Share', icon: Share2 },
    { type: 'divider' },
    { id: 'delete', label: 'Move to trash', icon: Trash2, danger: true },
  ]

  const folderActions = [
    { id: 'open', label: 'Open', icon: FolderOpen },
    { type: 'divider' },
    { id: 'rename', label: 'Rename', icon: Pencil },
    { id: 'move', label: 'Move to...', icon: FolderInput },
    { type: 'divider' },
    {
      id: 'star',
      label: item?.is_starred ? 'Remove from starred' : 'Add to starred',
      icon: item?.is_starred ? StarOff : Star,
    },
    { id: 'share', label: 'Share', icon: Share2 },
    { type: 'divider' },
    { id: 'delete', label: 'Move to trash', icon: Trash2, danger: true },
  ]

  const trashActions = [
    { id: 'restore', label: 'Restore', icon: RotateCcw },
    { id: 'delete-forever', label: 'Delete forever', icon: Trash2, danger: true },
  ]

  const actions =
    itemType === 'trash' ? trashActions : itemType === 'folder' ? folderActions : fileActions

  return (
    <div className={`relative ${className}`}>
      <button
        ref={buttonRef}
        onClick={(e) => {
          e.stopPropagation()
          setIsOpen(!isOpen)
        }}
        className="p-1.5 rounded-lg hover:bg-gray-200 text-gray-500"
        title="More actions"
      >
        <MoreVertical className="h-4 w-4" />
      </button>

      {isOpen && (
        <div
          ref={dropdownRef}
          className="absolute right-0 top-full mt-1 z-50 bg-white rounded-lg shadow-lg border border-gray-200 py-1 min-w-[180px] animate-dropdown"
        >
          {actions.map((action, index) => {
            if (action.type === 'divider') {
              return <div key={index} className="border-t border-gray-100 my-1" />
            }

            const Icon = action.icon

            return (
              <button
                key={action.id}
                onClick={(e) => {
                  e.stopPropagation()
                  handleAction(action.id)
                }}
                className={`w-full flex items-center gap-3 px-3 py-2 text-sm hover:bg-gray-50 ${
                  action.danger ? 'text-red-600 hover:bg-red-50' : 'text-gray-700'
                }`}
              >
                <Icon className="h-4 w-4" />
                <span>{action.label}</span>
              </button>
            )
          })}
        </div>
      )}

      <style>{`
        @keyframes dropdown {
          from {
            opacity: 0;
            transform: translateY(-4px);
          }
          to {
            opacity: 1;
            transform: translateY(0);
          }
        }
        .animate-dropdown {
          animation: dropdown 0.1s ease-out;
        }
      `}</style>
    </div>
  )
}
