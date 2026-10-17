import { X, Trash2, FolderInput, Copy, Star, Download, Share2 } from 'lucide-react'

export default function BulkActionBar({
  selectedCount,
  onClearSelection,
  onMove,
  onCopy,
  onStar,
  onDelete,
  onDownload,
  onShare,
  hasFiles = false, // true if selection includes files
  hasFolders = false, // true if selection includes folders
}) {
  if (selectedCount === 0) return null

  return (
    <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-40 animate-slide-up">
      <div className="bg-gray-900 text-white rounded-lg shadow-xl px-4 py-3 flex items-center gap-4">
        {/* Selection count */}
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">
            {selectedCount} {selectedCount === 1 ? 'item' : 'items'} selected
          </span>
          <button
            onClick={onClearSelection}
            className="p-1 hover:bg-gray-700 rounded"
            title="Clear selection"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="w-px h-6 bg-gray-600" />

        {/* Actions */}
        <div className="flex items-center gap-1">
          {hasFiles && (
            <button
              onClick={onDownload}
              className="flex items-center gap-2 px-3 py-1.5 text-sm hover:bg-gray-700 rounded-lg transition-colors"
              title="Download"
            >
              <Download className="h-4 w-4" />
              <span className="hidden sm:inline">Download</span>
            </button>
          )}

          <button
            onClick={onMove}
            className="flex items-center gap-2 px-3 py-1.5 text-sm hover:bg-gray-700 rounded-lg transition-colors"
            title="Move to"
          >
            <FolderInput className="h-4 w-4" />
            <span className="hidden sm:inline">Move</span>
          </button>

          {hasFiles && !hasFolders && (
            <button
              onClick={onCopy}
              className="flex items-center gap-2 px-3 py-1.5 text-sm hover:bg-gray-700 rounded-lg transition-colors"
              title="Make a copy"
            >
              <Copy className="h-4 w-4" />
              <span className="hidden sm:inline">Copy</span>
            </button>
          )}

          {hasFiles && (
            <button
              onClick={onStar}
              className="flex items-center gap-2 px-3 py-1.5 text-sm hover:bg-gray-700 rounded-lg transition-colors"
              title="Star"
            >
              <Star className="h-4 w-4" />
              <span className="hidden sm:inline">Star</span>
            </button>
          )}

          <button
            onClick={onShare}
            className="flex items-center gap-2 px-3 py-1.5 text-sm hover:bg-gray-700 rounded-lg transition-colors"
            title="Share"
          >
            <Share2 className="h-4 w-4" />
            <span className="hidden sm:inline">Share</span>
          </button>

          <div className="w-px h-6 bg-gray-600 mx-1" />

          <button
            onClick={onDelete}
            className="flex items-center gap-2 px-3 py-1.5 text-sm hover:bg-red-600 rounded-lg transition-colors"
            title="Move to trash"
          >
            <Trash2 className="h-4 w-4" />
            <span className="hidden sm:inline">Delete</span>
          </button>
        </div>
      </div>

      <style>{`
        @keyframes slide-up {
          from {
            transform: translate(-50%, 100%);
            opacity: 0;
          }
          to {
            transform: translate(-50%, 0);
            opacity: 1;
          }
        }
        .animate-slide-up {
          animation: slide-up 0.2s ease-out;
        }
      `}</style>
    </div>
  )
}
