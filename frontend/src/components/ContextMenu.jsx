import { useEffect, useRef } from 'react'
import {
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
  FileText,
} from 'lucide-react'

const MENU_ITEMS = {
  file: [
    { id: 'preview', label: 'Preview', icon: Eye, shortcut: 'Enter' },
    { id: 'download', label: 'Download', icon: Download, shortcut: 'Ctrl+D' },
    { type: 'divider' },
    { id: 'rename', label: 'Rename', icon: Pencil, shortcut: 'F2' },
    { id: 'move', label: 'Move to...', icon: FolderInput },
    { id: 'copy', label: 'Make a copy', icon: Copy },
    { type: 'divider' },
    { id: 'star', label: 'Add to starred', icon: Star, toggleLabel: 'Remove from starred', toggleIcon: StarOff },
    { id: 'share', label: 'Share', icon: Share2 },
    { type: 'divider' },
    { id: 'delete', label: 'Move to trash', icon: Trash2, danger: true, shortcut: 'Del' },
  ],
  folder: [
    { id: 'open', label: 'Open', icon: FolderOpen, shortcut: 'Enter' },
    { type: 'divider' },
    { id: 'rename', label: 'Rename', icon: Pencil, shortcut: 'F2' },
    { id: 'move', label: 'Move to...', icon: FolderInput },
    { type: 'divider' },
    { id: 'star', label: 'Add to starred', icon: Star, toggleLabel: 'Remove from starred', toggleIcon: StarOff },
    { id: 'share', label: 'Share', icon: Share2 },
    { type: 'divider' },
    { id: 'delete', label: 'Move to trash', icon: Trash2, danger: true, shortcut: 'Del' },
  ],
  trash: [
    { id: 'restore', label: 'Restore', icon: FolderOpen },
    { id: 'delete-forever', label: 'Delete forever', icon: Trash2, danger: true },
  ],
}

export default function ContextMenu({
  x,
  y,
  item,
  itemType = 'file', // 'file' | 'folder' | 'trash'
  onAction,
  onClose,
}) {
  const menuRef = useRef(null)

  useEffect(() => {
    const handleClickOutside = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) {
        onClose()
      }
    }

    const handleEscape = (e) => {
      if (e.key === 'Escape') {
        onClose()
      }
    }

    document.addEventListener('mousedown', handleClickOutside)
    document.addEventListener('keydown', handleEscape)

    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('keydown', handleEscape)
    }
  }, [onClose])

  // Adjust position to stay within viewport
  useEffect(() => {
    if (menuRef.current) {
      const rect = menuRef.current.getBoundingClientRect()
      const viewportWidth = window.innerWidth
      const viewportHeight = window.innerHeight

      let adjustedX = x
      let adjustedY = y

      if (x + rect.width > viewportWidth) {
        adjustedX = viewportWidth - rect.width - 8
      }
      if (y + rect.height > viewportHeight) {
        adjustedY = viewportHeight - rect.height - 8
      }

      menuRef.current.style.left = `${adjustedX}px`
      menuRef.current.style.top = `${adjustedY}px`
    }
  }, [x, y])

  const menuItems = MENU_ITEMS[itemType] || MENU_ITEMS.file

  const handleItemClick = (action) => {
    onAction(action, item)
    onClose()
  }

  return (
    <div
      ref={menuRef}
      className="fixed z-50 bg-white rounded-lg shadow-lg border border-gray-200 py-1 min-w-[200px] animate-fade-in"
      style={{ left: x, top: y }}
    >
      {menuItems.map((menuItem, index) => {
        if (menuItem.type === 'divider') {
          return <div key={index} className="border-t border-gray-100 my-1" />
        }

        // Handle star toggle
        const isStarred = item?.is_starred
        const label = menuItem.id === 'star' && isStarred ? menuItem.toggleLabel : menuItem.label
        const Icon = menuItem.id === 'star' && isStarred ? menuItem.toggleIcon : menuItem.icon

        return (
          <button
            key={menuItem.id}
            onClick={() => handleItemClick(menuItem.id)}
            className={`w-full flex items-center gap-3 px-3 py-2 text-sm hover:bg-gray-50 ${
              menuItem.danger ? 'text-red-600 hover:bg-red-50' : 'text-gray-700'
            }`}
          >
            <Icon className="h-4 w-4" />
            <span className="flex-1 text-left">{label}</span>
            {menuItem.shortcut && (
              <span className="text-xs text-gray-400">{menuItem.shortcut}</span>
            )}
          </button>
        )
      })}

      <style>{`
        @keyframes fade-in {
          from {
            opacity: 0;
            transform: scale(0.95);
          }
          to {
            opacity: 1;
            transform: scale(1);
          }
        }
        .animate-fade-in {
          animation: fade-in 0.1s ease-out;
        }
      `}</style>
    </div>
  )
}
