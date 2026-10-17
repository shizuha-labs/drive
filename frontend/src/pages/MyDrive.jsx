import { useState, useCallback, useMemo } from 'react'
import { useParams, useNavigate, useSearchParams, Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useDropzone } from 'react-dropzone'
import {
  File,
  Folder,
  Image,
  FileText,
  Film,
  Music,
  Archive,
  Code,
  Upload,
  FolderPlus,
  Grid,
  List,
  Star,
  ChevronRight,
  X,
  MoreVertical,
  Check,
} from 'lucide-react'
import {
  getFiles,
  getRootFiles,
  getFolders,
  getRootFolders,
  getFolder,
  uploadFile,
  createFolder,
  deleteFile,
  deleteFolder,
  toggleStar,
  updateFile,
  updateFolder,
  moveFile,
  moveFolder,
  copyFile,
  downloadFileBlob,
} from '../services/api'
import LoadingSpinner from '../components/LoadingSpinner'
import ContextMenu from '../components/ContextMenu'
import ActionDropdown from '../components/ActionDropdown'
import RenameDialog from '../components/RenameDialog'
import MoveDialog from '../components/MoveDialog'
import CopyDialog from '../components/CopyDialog'
import ConfirmDialog from '../components/ConfirmDialog'
import BulkActionBar from '../components/BulkActionBar'
import ShareDialog from '../components/ShareDialog'
import FilePreviewModal from '../components/FilePreviewModal'
import { useToast } from '../components/Toast'

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

export default function MyDrive() {
  const { folderId } = useParams()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  // DRV-72: the SearchBar navigates to /my-drive?q=<query>; when present, the
  // My Drive list is filtered by filename (backend `search` param) instead of
  // showing the folder contents. Empty q is treated as no search.
  const searchQuery = (searchParams.get('q') || '').trim()
  const queryClient = useQueryClient()

  // DRV-93: every folder projection (grid, tree, sidebar root + children) must
  // refresh together. The sidebar uses ['root-folder-children'] and
  // ['folder-children', id] with staleTime:30s — trashing/moving/renaming a
  // folder must invalidate ALL of them or the trashed folder stays visible in
  // My Drive while also appearing in Trash.
  const invalidateFolderQueries = useCallback(() => {
    queryClient.invalidateQueries(['folders'])
    queryClient.invalidateQueries(['root-folder-children'])
    queryClient.invalidateQueries(['folder-children'])
  }, [queryClient])
  const toast = useToast()

  const [viewMode, setViewMode] = useState('grid')
  const [showNewFolderModal, setShowNewFolderModal] = useState(false)
  const [newFolderName, setNewFolderName] = useState('')
  const [uploadProgress, setUploadProgress] = useState({})

  // Context menu state
  const [contextMenu, setContextMenu] = useState(null)

  // Selection state
  const [selectedItems, setSelectedItems] = useState(new Set())

  // Dialog states
  const [renameDialog, setRenameDialog] = useState({ isOpen: false, item: null, type: 'file' })
  const [moveDialog, setMoveDialog] = useState({ isOpen: false, items: [] })
  const [copyDialog, setCopyDialog] = useState({ isOpen: false, items: [] })
  const [deleteDialog, setDeleteDialog] = useState({ isOpen: false, item: null, type: 'file' })
  const [bulkDeleteDialog, setBulkDeleteDialog] = useState({ isOpen: false, items: [] })
  const [shareDialog, setShareDialog] = useState({ isOpen: false, item: null, type: 'file' })
  const [previewFile, setPreviewFile] = useState(null)

  // Fetch current folder details
  const { data: currentFolder } = useQuery({
    queryKey: ['folder', folderId],
    queryFn: () => (folderId ? getFolder(folderId) : null),
    enabled: !!folderId,
  })

  // Fetch folders
  const { data: folders, isLoading: foldersLoading } = useQuery({
    queryKey: ['folders', folderId, searchQuery],
    queryFn: () =>
      searchQuery
        ? getFolders({ search: searchQuery })
        : folderId
          ? getFolders({ parent: folderId })
          : getRootFolders(),
  })

  // Fetch files
  const { data: files, isLoading: filesLoading } = useQuery({
    queryKey: ['files', folderId, searchQuery],
    queryFn: () =>
      searchQuery
        ? getFiles({ search: searchQuery })
        : folderId
          ? getFiles({ folder: folderId })
          : getRootFiles(),
  })

  // Upload mutation
  const uploadMutation = useMutation({
    mutationFn: ({ file }) =>
      uploadFile(file, folderId, (progress) => {
        setUploadProgress((prev) => ({ ...prev, [file.name]: progress }))
      }),
    onSuccess: () => {
      queryClient.invalidateQueries(['files', folderId])
      queryClient.invalidateQueries(['quota'])
      toast.success('File uploaded successfully')
    },
    onError: (error) => {
      toast.error(error.response?.data?.detail || 'Upload failed')
    },
    onSettled: (_, __, { file }) => {
      setUploadProgress((prev) => {
        const { [file.name]: _, ...rest } = prev
        return rest
      })
    },
  })

  // Create folder mutation
  const createFolderMutation = useMutation({
    mutationFn: createFolder,
    onSuccess: () => {
      invalidateFolderQueries()
      setShowNewFolderModal(false)
      setNewFolderName('')
      toast.success('Folder created successfully')
    },
    onError: (error) => {
      toast.error(error.response?.data?.detail || 'Failed to create folder')
    },
  })

  // Rename mutations
  const renameFileMutation = useMutation({
    mutationFn: ({ id, name }) => updateFile(id, { name }),
    onSuccess: () => {
      queryClient.invalidateQueries(['files', folderId])
      setRenameDialog({ isOpen: false, item: null, type: 'file' })
      toast.success('File renamed successfully')
    },
    onError: (error) => {
      toast.error(error.response?.data?.detail || 'Failed to rename file')
    },
  })

  const renameFolderMutation = useMutation({
    mutationFn: ({ id, name }) => updateFolder(id, { name }),
    onSuccess: () => {
      invalidateFolderQueries()
      setRenameDialog({ isOpen: false, item: null, type: 'folder' })
      toast.success('Folder renamed successfully')
    },
    onError: (error) => {
      toast.error(error.response?.data?.detail || 'Failed to rename folder')
    },
  })

  // Move mutations
  const moveFileMutation = useMutation({
    mutationFn: ({ id, folderId }) => moveFile(id, folderId),
    onSuccess: () => {
      queryClient.invalidateQueries(['files'])
      queryClient.invalidateQueries(['folders', 'tree'])
      setMoveDialog({ isOpen: false, items: [] })
      toast.success('File moved successfully')
    },
    onError: (error) => {
      toast.error(error.response?.data?.detail || 'Failed to move file')
    },
  })

  const moveFolderMutation = useMutation({
    mutationFn: ({ id, parentId }) => moveFolder(id, parentId),
    onSuccess: () => {
      invalidateFolderQueries()
      setMoveDialog({ isOpen: false, items: [] })
      toast.success('Folder moved successfully')
    },
    onError: (error) => {
      toast.error(error.response?.data?.detail || 'Failed to move folder')
    },
  })

  // Copy mutation
  const copyFileMutation = useMutation({
    mutationFn: ({ id, folderId }) => copyFile(id, folderId),
    onSuccess: () => {
      queryClient.invalidateQueries(['files'])
      queryClient.invalidateQueries(['quota'])
      setCopyDialog({ isOpen: false, items: [] })
      toast.success('File copied successfully')
    },
    onError: (error) => {
      toast.error(error.response?.data?.detail || 'Failed to copy file')
    },
  })

  // Delete mutations
  const deleteFileMutation = useMutation({
    mutationFn: deleteFile,
    onSuccess: () => {
      queryClient.invalidateQueries(['files', folderId])
      setDeleteDialog({ isOpen: false, item: null, type: 'file' })
      toast.success('File moved to trash')
    },
    onError: (error) => {
      toast.error(error.response?.data?.detail || 'Failed to delete file')
    },
  })

  const deleteFolderMutation = useMutation({
    mutationFn: deleteFolder,
    onSuccess: () => {
      invalidateFolderQueries()
      setDeleteDialog({ isOpen: false, item: null, type: 'folder' })
      toast.success('Folder moved to trash')
    },
    onError: (error) => {
      toast.error(error.response?.data?.detail || 'Failed to delete folder')
    },
  })

  // Star toggle
  const starMutation = useMutation({
    mutationFn: toggleStar,
    onSuccess: (data) => {
      queryClient.invalidateQueries(['files', folderId])
      toast.success(data.is_starred ? 'Added to starred' : 'Removed from starred')
    },
    onError: (error) => {
      toast.error(error.response?.data?.detail || 'Failed to update star')
    },
  })

  // Selection helpers
  const getSelectedItemsData = useMemo(() => {
    const items = []
    selectedItems.forEach(key => {
      const [type, id] = key.split(':')
      if (type === 'file') {
        const file = files?.results?.find(f => f.id === id)
        if (file) items.push({ ...file, type: 'file' })
      } else if (type === 'folder') {
        const folder = folders?.results?.find(f => f.id === id)
        if (folder) items.push({ ...folder, type: 'folder' })
      }
    })
    return items
  }, [selectedItems, files?.results, folders?.results])

  const hasSelectedFiles = getSelectedItemsData.some(i => i.type === 'file')
  const hasSelectedFolders = getSelectedItemsData.some(i => i.type === 'folder')

  const toggleSelection = (item, type) => {
    const key = `${type}:${item.id}`
    setSelectedItems(prev => {
      const newSet = new Set(prev)
      if (newSet.has(key)) {
        newSet.delete(key)
      } else {
        newSet.add(key)
      }
      return newSet
    })
  }

  const isSelected = (item, type) => {
    return selectedItems.has(`${type}:${item.id}`)
  }

  const clearSelection = () => {
    setSelectedItems(new Set())
  }

  const selectAll = () => {
    const keys = new Set()
    folders?.results?.forEach(f => keys.add(`folder:${f.id}`))
    files?.results?.forEach(f => keys.add(`file:${f.id}`))
    setSelectedItems(keys)
  }

  // Dropzone
  const onDrop = useCallback(
    (acceptedFiles) => {
      acceptedFiles.forEach((file) => {
        uploadMutation.mutate({ file })
      })
    },
    [folderId]
  )

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    noClick: true,
  })

  // Build breadcrumb path
  const breadcrumbs = []
  if (currentFolder) {
    let folder = currentFolder
    while (folder) {
      breadcrumbs.unshift(folder)
      folder = folder.parent_data
    }
  }

  const handleCreateFolder = () => {
    if (newFolderName.trim()) {
      createFolderMutation.mutate({
        name: newFolderName.trim(),
        parent: folderId || null,
      })
    }
  }

  // Context menu handler
  const handleContextMenu = (e, item, type) => {
    e.preventDefault()
    e.stopPropagation()
    setContextMenu({
      x: e.clientX,
      y: e.clientY,
      item,
      type,
    })
  }

  // Handle context menu action
  const handleContextAction = (action, item) => {
    const type = item.file_type !== undefined ? 'file' : 'folder'

    switch (action) {
      case 'open':
        if (type === 'folder') {
          navigate(`/my-drive/${item.id}`)
        }
        break
      case 'preview':
        if (type === 'file') {
          setPreviewFile(item)
        }
        break
      case 'download':
        if (type === 'file') {
          downloadFileBlob(item.id, item.name).catch(() =>
            toast.error(`Could not download ${item.name}`)
          )
        }
        break
      case 'rename':
        setRenameDialog({ isOpen: true, item, type })
        break
      case 'move':
        setMoveDialog({ isOpen: true, items: [{ ...item, type }] })
        break
      case 'copy':
        if (type === 'file') {
          setCopyDialog({ isOpen: true, items: [{ ...item, type }] })
        } else {
          toast.warning('Folders cannot be copied')
        }
        break
      case 'star':
        if (type === 'file') {
          starMutation.mutate(item.id)
        }
        break
      case 'share':
        setShareDialog({ isOpen: true, item, type })
        break
      case 'delete':
        setDeleteDialog({ isOpen: true, item, type })
        break
      default:
        break
    }
  }

  // Handle rename
  const handleRename = (item, newName) => {
    if (renameDialog.type === 'file') {
      renameFileMutation.mutate({ id: item.id, name: newName })
    } else {
      renameFolderMutation.mutate({ id: item.id, name: newName })
    }
  }

  // Handle move
  const handleMove = (items, destinationFolderId) => {
    items.forEach(item => {
      if (item.type === 'file') {
        moveFileMutation.mutate({ id: item.id, folderId: destinationFolderId })
      } else {
        moveFolderMutation.mutate({ id: item.id, parentId: destinationFolderId })
      }
    })
  }

  // Handle copy
  const handleCopy = (items, destinationFolderId) => {
    items.forEach(item => {
      if (item.type === 'file') {
        copyFileMutation.mutate({ id: item.id, folderId: destinationFolderId })
      }
    })
  }

  // Handle delete confirmation
  const handleDeleteConfirm = () => {
    if (deleteDialog.type === 'file') {
      deleteFileMutation.mutate(deleteDialog.item.id)
    } else {
      deleteFolderMutation.mutate(deleteDialog.item.id)
    }
  }

  // Bulk action handlers
  const handleBulkMove = () => {
    setMoveDialog({ isOpen: true, items: getSelectedItemsData })
  }

  const handleBulkCopy = () => {
    const filesToCopy = getSelectedItemsData.filter(i => i.type === 'file')
    if (filesToCopy.length > 0) {
      setCopyDialog({ isOpen: true, items: filesToCopy })
    }
  }

  const handleBulkStar = () => {
    const filesToStar = getSelectedItemsData.filter(i => i.type === 'file')
    filesToStar.forEach(file => {
      starMutation.mutate(file.id)
    })
    clearSelection()
  }

  const handleBulkDelete = () => {
    setBulkDeleteDialog({ isOpen: true, items: getSelectedItemsData })
  }

  const handleBulkDeleteConfirm = () => {
    bulkDeleteDialog.items.forEach(item => {
      if (item.type === 'file') {
        deleteFileMutation.mutate(item.id)
      } else {
        deleteFolderMutation.mutate(item.id)
      }
    })
    setBulkDeleteDialog({ isOpen: false, items: [] })
    clearSelection()
  }

  const handleBulkDownload = async () => {
    const filesToDownload = getSelectedItemsData.filter(i => i.type === 'file')
    // Sequential, not Promise.all: browsers throttle/ignore many simultaneous
    // programmatic saves, which silently dropped files from a bulk download.
    for (const file of filesToDownload) {
      try {
        await downloadFileBlob(file.id, file.name)
      } catch {
        toast.error(`Could not download ${file.name}`)
      }
    }
  }

  const handleBulkShare = () => {
    toast.info('Share feature coming soon')
  }

  const isLoading = foldersLoading || filesLoading

  return (
    <div {...getRootProps()} className="min-h-[calc(100vh-8rem)]">
      <input {...getInputProps()} />

      {/* Drag overlay */}
      {isDragActive && (
        <div className="fixed inset-0 bg-blue-500 bg-opacity-10 border-2 border-dashed border-blue-500 z-50 flex items-center justify-center">
          <div className="bg-white p-8 rounded-lg shadow-lg text-center">
            <Upload className="h-12 w-12 text-blue-500 mx-auto mb-4" />
            <p className="text-lg font-medium text-gray-900">Drop files to upload</p>
          </div>
        </div>
      )}

      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-4 mb-6">
        <div>
          {/* Breadcrumbs */}
          <nav className="flex items-center space-x-2 text-sm">
            <Link to="/my-drive" className="text-gray-600 hover:text-gray-900">
              My Drive
            </Link>
            {breadcrumbs.map((folder, i) => (
              <span key={folder.id} className="flex items-center">
                <ChevronRight className="h-4 w-4 text-gray-400" />
                {i === breadcrumbs.length - 1 ? (
                  <span className="text-gray-900 font-medium">{folder.name}</span>
                ) : (
                  <Link
                    to={`/my-drive/${folder.id}`}
                    className="text-gray-600 hover:text-gray-900"
                  >
                    {folder.name}
                  </Link>
                )}
              </span>
            ))}
          </nav>
          <h1 className="text-2xl font-semibold text-gray-900 mt-2">
            {currentFolder ? currentFolder.name : 'My Drive'}
          </h1>
        </div>

        <div className="flex items-center space-x-2">
          <label className="flex items-center space-x-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 cursor-pointer">
            <Upload size={18} />
            <span>Upload</span>
            <input
              type="file"
              multiple
              className="hidden"
              onChange={(e) => {
                Array.from(e.target.files).forEach((file) => {
                  uploadMutation.mutate({ file })
                })
                e.target.value = ''
              }}
            />
          </label>

          <button
            onClick={() => setShowNewFolderModal(true)}
            className="flex items-center space-x-2 px-4 py-2 border border-gray-200 rounded-lg hover:bg-gray-50"
          >
            <FolderPlus size={18} />
            <span>New Folder</span>
          </button>

          <div className="flex items-center border border-gray-200 rounded-lg">
            <button
              onClick={() => setViewMode('grid')}
              className={`p-2 ${viewMode === 'grid' ? 'bg-gray-100' : ''}`}
            >
              <Grid size={18} />
            </button>
            <button
              onClick={() => setViewMode('list')}
              className={`p-2 ${viewMode === 'list' ? 'bg-gray-100' : ''}`}
            >
              <List size={18} />
            </button>
          </div>
        </div>
      </div>

      {/* Upload Progress */}
      {Object.keys(uploadProgress).length > 0 && (
        <div className="mb-4 p-4 bg-white rounded-lg shadow-sm border border-gray-200">
          <h3 className="text-sm font-medium text-gray-900 mb-2">Uploading...</h3>
          {Object.entries(uploadProgress).map(([name, progress]) => (
            <div key={name} className="mb-2">
              <div className="flex justify-between text-sm text-gray-600 mb-1">
                <span className="truncate">{name}</span>
                <span>{progress}%</span>
              </div>
              <div className="w-full bg-gray-200 rounded-full h-2">
                <div
                  className="bg-blue-600 h-2 rounded-full transition-all"
                  style={{ width: `${progress}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      )}

      {isLoading ? (
        <div className="flex justify-center items-center h-64">
          <LoadingSpinner size="lg" />
        </div>
      ) : (
        <>
          {/* Folders */}
          {folders?.results && folders.results.length > 0 && (
            <div className="mb-6">
              <h2 className="text-sm font-medium text-gray-500 mb-3">Folders</h2>
              <div
                className={
                  viewMode === 'grid'
                    ? 'grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4'
                    : 'space-y-2'
                }
              >
                {folders.results.map((folder) => (
                  <div
                    key={folder.id}
                    onContextMenu={(e) => handleContextMenu(e, folder, 'folder')}
                    onDoubleClick={() => navigate(`/my-drive/${folder.id}`)}
                    onClick={(e) => {
                      if (e.ctrlKey || e.metaKey) {
                        e.preventDefault()
                        toggleSelection(folder, 'folder')
                      }
                    }}
                    className={`cursor-pointer group relative ${
                      viewMode === 'grid'
                        ? 'bg-white rounded-lg border p-4 hover:shadow-md transition-shadow'
                        : 'flex items-center bg-white rounded-lg border px-4 py-3 hover:shadow-md'
                    } ${
                      isSelected(folder, 'folder')
                        ? 'border-blue-500 bg-blue-50'
                        : 'border-gray-200'
                    }`}
                  >
                    {/* Selection checkbox */}
                    <div
                      className={`${
                        viewMode === 'grid'
                          ? 'absolute top-2 left-2'
                          : 'mr-3'
                      } ${
                        isSelected(folder, 'folder') ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
                      } transition-opacity`}
                    >
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          toggleSelection(folder, 'folder')
                        }}
                        className={`w-5 h-5 rounded border flex items-center justify-center ${
                          isSelected(folder, 'folder')
                            ? 'bg-blue-600 border-blue-600 text-white'
                            : 'border-gray-300 bg-white hover:border-blue-500'
                        }`}
                      >
                        {isSelected(folder, 'folder') && <Check className="h-3 w-3" />}
                      </button>
                    </div>

                    <Folder
                      className={`text-yellow-500 ${
                        viewMode === 'grid' ? 'h-12 w-12 mb-2' : 'h-6 w-6 mr-3'
                      }`}
                      fill="currentColor"
                    />
                    <span className="text-sm font-medium text-gray-900 truncate flex-1">
                      {folder.name}
                    </span>

                    {/* Action button */}
                    <div
                      className={`${
                        viewMode === 'grid'
                          ? 'absolute top-2 right-2'
                          : ''
                      } opacity-0 group-hover:opacity-100 transition-opacity`}
                    >
                      <ActionDropdown
                        item={folder}
                        itemType="folder"
                        onAction={handleContextAction}
                      />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Files */}
          {files?.results && files.results.length > 0 && (
            <div>
              <h2 className="text-sm font-medium text-gray-500 mb-3">Files</h2>
              <div
                className={
                  viewMode === 'grid'
                    ? 'grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4'
                    : 'space-y-2'
                }
              >
                {files.results.map((file) => {
                  const Icon = fileTypeIcons[file.file_type] || File
                  return (
                    <div
                      key={file.id}
                      data-testid="file-row"
                      onContextMenu={(e) => handleContextMenu(e, file, 'file')}
                      onDoubleClick={() => setPreviewFile(file)}
                      onClick={(e) => {
                        if (e.ctrlKey || e.metaKey) {
                          e.preventDefault()
                          toggleSelection(file, 'file')
                        }
                      }}
                      className={`group relative ${
                        viewMode === 'grid'
                          ? 'bg-white rounded-lg border p-4 hover:shadow-md transition-shadow'
                          : 'flex items-center bg-white rounded-lg border px-4 py-3 hover:shadow-md'
                      } ${
                        isSelected(file, 'file')
                          ? 'border-blue-500 bg-blue-50'
                          : 'border-gray-200'
                      }`}
                    >
                      {/* Selection checkbox */}
                      <div
                        className={`${
                          viewMode === 'grid'
                            ? 'absolute top-2 left-2'
                            : 'mr-3'
                        } ${
                          isSelected(file, 'file') ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
                        } transition-opacity`}
                      >
                        <button
                          onClick={(e) => {
                            e.stopPropagation()
                            toggleSelection(file, 'file')
                          }}
                          className={`w-5 h-5 rounded border flex items-center justify-center ${
                            isSelected(file, 'file')
                              ? 'bg-blue-600 border-blue-600 text-white'
                              : 'border-gray-300 bg-white hover:border-blue-500'
                          }`}
                        >
                          {isSelected(file, 'file') && <Check className="h-3 w-3" />}
                        </button>
                      </div>

                      {/* Thumbnail or Icon */}
                      {file.has_thumbnail && file.thumbnail_url ? (
                        <img
                          src={file.thumbnail_url}
                          alt={file.name}
                          className={`object-cover rounded ${
                            viewMode === 'grid' ? 'h-20 w-full mb-2' : 'h-10 w-10 mr-3'
                          }`}
                        />
                      ) : (
                        <Icon
                          className={`text-gray-400 ${
                            viewMode === 'grid' ? 'h-12 w-12 mb-2' : 'h-6 w-6 mr-3'
                          }`}
                        />
                      )}

                      <div className={viewMode === 'list' ? 'flex-1' : ''}>
                        <div className="text-sm font-medium text-gray-900 truncate">
                          {file.name}
                        </div>
                        <div className="text-xs text-gray-500 flex items-center gap-2">
                          <span>{file.size_human}</span>
                          {file.is_starred && (
                            <Star size={12} className="text-yellow-500 fill-current" />
                          )}
                        </div>
                      </div>

                      {/* Actions */}
                      <div
                        className={`${
                          viewMode === 'grid'
                            ? 'absolute top-2 right-2'
                            : ''
                        } opacity-0 group-hover:opacity-100 transition-opacity flex items-center gap-1`}
                      >
                        <button
                          onClick={(e) => {
                            e.stopPropagation()
                            starMutation.mutate(file.id)
                          }}
                          className="p-1 rounded hover:bg-gray-100"
                        >
                          <Star
                            size={16}
                            className={file.is_starred ? 'text-yellow-500 fill-current' : 'text-gray-400'}
                          />
                        </button>
                        <ActionDropdown
                          item={file}
                          itemType="file"
                          onAction={handleContextAction}
                        />
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* Empty state */}
          {(!folders?.results || folders.results.length === 0) &&
            (!files?.results || files.results.length === 0) && (
              <div className="text-center py-16">
                <Folder className="h-16 w-16 text-gray-300 mx-auto mb-4" />
                <h3 className="text-lg font-medium text-gray-900 mb-2">
                  {searchQuery ? 'No results found' : 'This folder is empty'}
                </h3>
                <p className="text-gray-500 mb-4">
                  {searchQuery
                    ? `Nothing in your Drive matches "${searchQuery}"`
                    : 'Drag and drop files here, or use the upload button'}
                </p>
              </div>
            )}
        </>
      )}

      {/* Context Menu */}
      {contextMenu && (
        <ContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          item={contextMenu.item}
          itemType={contextMenu.type}
          onAction={handleContextAction}
          onClose={() => setContextMenu(null)}
        />
      )}

      {/* New Folder Modal */}
      {showNewFolderModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg p-6 w-96">
            <div className="flex justify-between items-center mb-4">
              <h3 className="text-lg font-medium">New Folder</h3>
              <button
                onClick={() => setShowNewFolderModal(false)}
                className="text-gray-400 hover:text-gray-600"
              >
                <X size={20} />
              </button>
            </div>
            <input
              type="text"
              value={newFolderName}
              onChange={(e) => setNewFolderName(e.target.value)}
              placeholder="Folder name"
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
              autoFocus
              onKeyDown={(e) => e.key === 'Enter' && handleCreateFolder()}
            />
            <div className="flex justify-end space-x-2 mt-4">
              <button
                onClick={() => setShowNewFolderModal(false)}
                className="px-4 py-2 text-gray-600 hover:bg-gray-100 rounded-lg"
              >
                Cancel
              </button>
              <button
                onClick={handleCreateFolder}
                disabled={!newFolderName.trim()}
                className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
              >
                Create
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Rename Dialog */}
      <RenameDialog
        isOpen={renameDialog.isOpen}
        onClose={() => setRenameDialog({ isOpen: false, item: null, type: 'file' })}
        onRename={handleRename}
        item={renameDialog.item}
        itemType={renameDialog.type}
        isLoading={renameFileMutation.isLoading || renameFolderMutation.isLoading}
      />

      {/* Move Dialog */}
      <MoveDialog
        isOpen={moveDialog.isOpen}
        onClose={() => setMoveDialog({ isOpen: false, items: [] })}
        onMove={handleMove}
        items={moveDialog.items}
        isLoading={moveFileMutation.isLoading || moveFolderMutation.isLoading}
      />

      {/* Copy Dialog */}
      <CopyDialog
        isOpen={copyDialog.isOpen}
        onClose={() => setCopyDialog({ isOpen: false, items: [] })}
        onCopy={handleCopy}
        items={copyDialog.items}
        isLoading={copyFileMutation.isLoading}
      />

      {/* Delete Confirmation Dialog */}
      <ConfirmDialog
        isOpen={deleteDialog.isOpen}
        onClose={() => setDeleteDialog({ isOpen: false, item: null, type: 'file' })}
        onConfirm={handleDeleteConfirm}
        title={`Move to Trash?`}
        message={`"${deleteDialog.item?.name}" will be moved to trash. You can restore it from trash later.`}
        confirmText="Move to Trash"
        variant="danger"
        isLoading={deleteFileMutation.isLoading || deleteFolderMutation.isLoading}
      />

      {/* Bulk Delete Confirmation Dialog */}
      <ConfirmDialog
        isOpen={bulkDeleteDialog.isOpen}
        onClose={() => setBulkDeleteDialog({ isOpen: false, items: [] })}
        onConfirm={handleBulkDeleteConfirm}
        title={`Move ${bulkDeleteDialog.items.length} items to Trash?`}
        message={`${bulkDeleteDialog.items.length} items will be moved to trash. You can restore them from trash later.`}
        confirmText="Move to Trash"
        variant="danger"
        isLoading={deleteFileMutation.isLoading || deleteFolderMutation.isLoading}
      />

      {/* Share Dialog */}
      <ShareDialog
        isOpen={shareDialog.isOpen}
        onClose={() => setShareDialog({ isOpen: false, item: null, type: 'file' })}
        item={shareDialog.item}
        itemType={shareDialog.type}
      />

      {/* File Preview Modal */}
      <FilePreviewModal
        isOpen={!!previewFile}
        onClose={() => setPreviewFile(null)}
        file={previewFile}
        files={files?.results || []}
        onNavigate={setPreviewFile}
        onShare={(file) => setShareDialog({ isOpen: true, item: file, type: 'file' })}
      />

      {/* Bulk Action Bar */}
      <BulkActionBar
        selectedCount={selectedItems.size}
        onClearSelection={clearSelection}
        onMove={handleBulkMove}
        onCopy={handleBulkCopy}
        onStar={handleBulkStar}
        onDelete={handleBulkDelete}
        onDownload={handleBulkDownload}
        onShare={handleBulkShare}
        hasFiles={hasSelectedFiles}
        hasFolders={hasSelectedFolders}
      />
    </div>
  )
}
