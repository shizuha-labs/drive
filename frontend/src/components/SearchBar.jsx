import { useState, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  Search,
  X,
  File,
  Folder,
  Image,
  FileText,
  Film,
  Music,
  Archive,
  Code,
  Loader2,
} from 'lucide-react'
import { getFiles, getFolders } from '../services/api'

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

function useDebounce(value, delay) {
  const [debouncedValue, setDebouncedValue] = useState(value)

  useEffect(() => {
    const handler = setTimeout(() => {
      setDebouncedValue(value)
    }, delay)

    return () => {
      clearTimeout(handler)
    }
  }, [value, delay])

  return debouncedValue
}

export default function SearchBar({ onFileClick, onFolderClick }) {
  const [query, setQuery] = useState('')
  const [isOpen, setIsOpen] = useState(false)
  const [selectedIndex, setSelectedIndex] = useState(-1)
  const inputRef = useRef(null)
  const dropdownRef = useRef(null)
  const navigate = useNavigate()

  const debouncedQuery = useDebounce(query, 300)

  // Search files
  const { data: fileResults, isLoading: filesLoading } = useQuery({
    queryKey: ['search', 'files', debouncedQuery],
    queryFn: () => getFiles({ search: debouncedQuery }),
    enabled: debouncedQuery.length >= 2,
  })

  // Search folders
  const { data: folderResults, isLoading: foldersLoading } = useQuery({
    queryKey: ['search', 'folders', debouncedQuery],
    queryFn: () => getFolders({ search: debouncedQuery }),
    enabled: debouncedQuery.length >= 2,
  })

  const isLoading = filesLoading || foldersLoading
  const files = fileResults?.results || []
  const folders = folderResults?.results || []
  const hasResults = files.length > 0 || folders.length > 0
  const allResults = [...folders.map(f => ({ ...f, _type: 'folder' })), ...files.map(f => ({ ...f, _type: 'file' }))]

  useEffect(() => {
    const handleClickOutside = (e) => {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(e.target) &&
        inputRef.current &&
        !inputRef.current.contains(e.target)
      ) {
        setIsOpen(false)
      }
    }

    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  useEffect(() => {
    setSelectedIndex(-1)
  }, [debouncedQuery])

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') {
      // DRV-72: Enter always navigates to the filtered My Drive view with the
      // current query — even when no dropdown result is selected. This gives a
      // consistent "search from My Drive" behavior: exact/partial filename
      // queries filter the list, and a nonmatch shows an honest empty result.
      e.preventDefault()
      const q = query.trim()
      if (q) {
        navigate(`/my-drive?q=${encodeURIComponent(q)}`)
        setQuery('')
        setIsOpen(false)
        inputRef.current?.blur()
      }
      return
    }
    if (!isOpen || !hasResults) return

    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault()
        setSelectedIndex(prev => Math.min(prev + 1, allResults.length - 1))
        break
      case 'ArrowUp':
        e.preventDefault()
        setSelectedIndex(prev => Math.max(prev - 1, 0))
        break
      case 'Escape':
        setIsOpen(false)
        inputRef.current?.blur()
        break
    }
  }

  const handleResultClick = (result) => {
    if (result._type === 'folder') {
      if (onFolderClick) {
        onFolderClick(result)
      } else {
        navigate(`/my-drive/${result.id}`)
      }
    } else {
      if (onFileClick) {
        onFileClick(result)
      }
    }
    setQuery('')
    setIsOpen(false)
  }

  const clearSearch = () => {
    setQuery('')
    setIsOpen(false)
    inputRef.current?.focus()
  }

  return (
    <div className="relative">
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-5 w-5 text-gray-400" />
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value)
            setIsOpen(true)
          }}
          onFocus={() => setIsOpen(true)}
          onKeyDown={handleKeyDown}
          placeholder="Search in Drive"
          className="w-full pl-10 pr-10 py-2 bg-gray-100 border border-gray-200 rounded-lg focus:bg-white focus:ring-2 focus:ring-blue-500 focus:border-transparent transition-colors"
        />
        {query && (
          <button
            onClick={clearSearch}
            className="absolute right-3 top-1/2 -translate-y-1/2 p-1 text-gray-400 hover:text-gray-600 rounded"
          >
            <X className="h-4 w-4" />
          </button>
        )}
      </div>

      {/* Dropdown */}
      {isOpen && query.length >= 2 && (
        <div
          ref={dropdownRef}
          className="absolute top-full left-0 right-0 mt-1 bg-white rounded-lg shadow-lg border border-gray-200 max-h-96 overflow-auto z-50"
        >
          {isLoading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-6 w-6 text-gray-400 animate-spin" />
            </div>
          ) : hasResults ? (
            <div className="py-2">
              {/* Folders */}
              {folders.length > 0 && (
                <>
                  <div className="px-3 py-1 text-xs font-medium text-gray-500 uppercase">
                    Folders
                  </div>
                  {folders.map((folder, idx) => (
                    <button
                      key={folder.id}
                      onClick={() => handleResultClick({ ...folder, _type: 'folder' })}
                      className={`w-full flex items-center gap-3 px-3 py-2 text-left hover:bg-gray-50 ${
                        selectedIndex === idx ? 'bg-gray-100' : ''
                      }`}
                    >
                      <Folder className="h-5 w-5 text-yellow-500" fill="currentColor" />
                      <div className="flex-1 min-w-0">
                        <div className="text-sm font-medium text-gray-900 truncate">
                          {folder.name}
                        </div>
                        {folder.path && (
                          <div className="text-xs text-gray-500 truncate">
                            {folder.path}
                          </div>
                        )}
                      </div>
                    </button>
                  ))}
                </>
              )}

              {/* Files */}
              {files.length > 0 && (
                <>
                  <div className="px-3 py-1 text-xs font-medium text-gray-500 uppercase mt-2">
                    Files
                  </div>
                  {files.map((file, idx) => {
                    const Icon = fileTypeIcons[file.file_type] || File
                    const resultIdx = folders.length + idx
                    return (
                      <button
                        key={file.id}
                        onClick={() => handleResultClick({ ...file, _type: 'file' })}
                        className={`w-full flex items-center gap-3 px-3 py-2 text-left hover:bg-gray-50 ${
                          selectedIndex === resultIdx ? 'bg-gray-100' : ''
                        }`}
                      >
                        {file.has_thumbnail && file.thumbnail_url ? (
                          <img
                            src={file.thumbnail_url}
                            alt=""
                            className="h-8 w-8 rounded object-cover"
                          />
                        ) : (
                          <Icon className="h-5 w-5 text-gray-400" />
                        )}
                        <div className="flex-1 min-w-0">
                          <div className="text-sm font-medium text-gray-900 truncate">
                            {file.name}
                          </div>
                          <div className="text-xs text-gray-500">
                            {file.size_human}
                            {file.folder_name && ` • ${file.folder_name}`}
                          </div>
                        </div>
                      </button>
                    )
                  })}
                </>
              )}
            </div>
          ) : (
            <div className="py-8 text-center text-gray-500">
              <Search className="h-8 w-8 mx-auto mb-2 text-gray-300" />
              <p className="text-sm">No results found for "{query}"</p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
