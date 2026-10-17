import { useState, useEffect } from 'react'
import {
  X,
  Download,
  Share2,
  ChevronLeft,
  ChevronRight,
  ZoomIn,
  ZoomOut,
  RotateCw,
  Maximize2,
  FileText,
  Film,
  Music,
  Image as ImageIcon,
  File,
  Loader2,
} from 'lucide-react'
import { downloadFileBlob, fetchFileObjectUrl, fetchFileText } from '../services/api'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { tomorrow } from 'react-syntax-highlighter/dist/esm/styles/prism'

const PREVIEW_TYPES = {
  image: ['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp', 'ico'],
  video: ['mp4', 'webm', 'ogg', 'mov', 'avi', 'mkv'],
  audio: ['mp3', 'wav', 'ogg', 'flac', 'aac', 'm4a'],
  pdf: ['pdf'],
  code: ['js', 'jsx', 'ts', 'tsx', 'py', 'java', 'cpp', 'c', 'h', 'cs', 'go', 'rs', 'rb', 'php', 'html', 'css', 'scss', 'less', 'json', 'xml', 'yaml', 'yml', 'md', 'sql', 'sh', 'bash', 'ps1', 'bat'],
  text: ['txt', 'log', 'csv', 'env', 'gitignore', 'dockerignore'],
}

const LANGUAGE_MAP = {
  js: 'javascript',
  jsx: 'jsx',
  ts: 'typescript',
  tsx: 'tsx',
  py: 'python',
  java: 'java',
  cpp: 'cpp',
  c: 'c',
  h: 'c',
  cs: 'csharp',
  go: 'go',
  rs: 'rust',
  rb: 'ruby',
  php: 'php',
  html: 'html',
  css: 'css',
  scss: 'scss',
  less: 'less',
  json: 'json',
  xml: 'xml',
  yaml: 'yaml',
  yml: 'yaml',
  md: 'markdown',
  sql: 'sql',
  sh: 'bash',
  bash: 'bash',
  ps1: 'powershell',
  bat: 'batch',
}

function getPreviewType(extension) {
  const ext = extension?.toLowerCase()
  for (const [type, extensions] of Object.entries(PREVIEW_TYPES)) {
    if (extensions.includes(ext)) return type
  }
  return null
}

function ImagePreview({ src, alt }) {
  const [zoom, setZoom] = useState(1)
  const [rotation, setRotation] = useState(0)

  const handleZoomIn = () => setZoom(prev => Math.min(prev + 0.25, 3))
  const handleZoomOut = () => setZoom(prev => Math.max(prev - 0.25, 0.5))
  const handleRotate = () => setRotation(prev => (prev + 90) % 360)

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-center gap-2 p-2 bg-gray-800">
        <button
          onClick={handleZoomOut}
          className="p-2 text-gray-300 hover:bg-gray-700 rounded"
          title="Zoom out"
        >
          <ZoomOut className="h-5 w-5" />
        </button>
        <span className="text-gray-300 text-sm min-w-[60px] text-center">
          {Math.round(zoom * 100)}%
        </span>
        <button
          onClick={handleZoomIn}
          className="p-2 text-gray-300 hover:bg-gray-700 rounded"
          title="Zoom in"
        >
          <ZoomIn className="h-5 w-5" />
        </button>
        <div className="w-px h-6 bg-gray-600 mx-2" />
        <button
          onClick={handleRotate}
          className="p-2 text-gray-300 hover:bg-gray-700 rounded"
          title="Rotate"
        >
          <RotateCw className="h-5 w-5" />
        </button>
      </div>
      <div className="flex-1 overflow-auto flex items-center justify-center bg-gray-900 p-4">
        <img
          src={src}
          alt={alt}
          style={{
            transform: `scale(${zoom}) rotate(${rotation}deg)`,
            transition: 'transform 0.2s ease',
            maxWidth: zoom > 1 ? 'none' : '100%',
            maxHeight: zoom > 1 ? 'none' : '100%',
          }}
          className="object-contain"
        />
      </div>
    </div>
  )
}

function VideoPreview({ src }) {
  return (
    <div className="flex-1 flex items-center justify-center bg-black p-4">
      <video
        src={src}
        controls
        autoPlay
        className="max-w-full max-h-full"
      >
        Your browser does not support video playback.
      </video>
    </div>
  )
}

function AudioPreview({ src, fileName }) {
  return (
    <div className="flex-1 flex flex-col items-center justify-center bg-gray-900 p-8">
      <Music className="h-24 w-24 text-gray-500 mb-8" />
      <h3 className="text-white text-lg font-medium mb-4">{fileName}</h3>
      <audio src={src} controls autoPlay className="w-full max-w-md">
        Your browser does not support audio playback.
      </audio>
    </div>
  )
}

function PdfPreview({ src }) {
  return (
    <div className="flex-1 bg-gray-200">
      <iframe
        src={src}
        title="PDF Preview"
        className="w-full h-full"
      />
    </div>
  )
}

function CodePreview({ content, extension, fileName }) {
  const language = LANGUAGE_MAP[extension?.toLowerCase()] || 'text'

  return (
    <div className="flex-1 overflow-auto bg-gray-900">
      <div className="p-2 bg-gray-800 text-gray-400 text-sm border-b border-gray-700">
        {fileName}
      </div>
      <SyntaxHighlighter
        language={language}
        style={tomorrow}
        showLineNumbers
        customStyle={{
          margin: 0,
          padding: '1rem',
          background: 'transparent',
          fontSize: '14px',
        }}
      >
        {content}
      </SyntaxHighlighter>
    </div>
  )
}

function TextPreview({ content, fileName }) {
  return (
    <div className="flex-1 overflow-auto bg-gray-900">
      <div className="p-2 bg-gray-800 text-gray-400 text-sm border-b border-gray-700">
        {fileName}
      </div>
      <pre className="p-4 text-gray-200 font-mono text-sm whitespace-pre-wrap">
        {content}
      </pre>
    </div>
  )
}

function UnsupportedPreview({ file }) {
  const Icon = file?.file_type === 'video' ? Film
    : file?.file_type === 'audio' ? Music
    : file?.file_type === 'image' ? ImageIcon
    : file?.file_type === 'document' ? FileText
    : File

  return (
    <div className="flex-1 flex flex-col items-center justify-center bg-gray-900 p-8">
      <Icon className="h-24 w-24 text-gray-500 mb-4" />
      <h3 className="text-white text-lg font-medium mb-2">{file?.name}</h3>
      <p className="text-gray-400 text-sm mb-6">
        Preview not available for this file type
      </p>
      <p className="text-gray-500 text-sm">
        {file?.size_human} • {file?.extension?.toUpperCase() || 'Unknown'} file
      </p>
    </div>
  )
}

export default function FilePreviewModal({
  isOpen,
  onClose,
  file,
  files = [], // List of files for navigation
  onNavigate,
  onShare,
}) {
  const [loading, setLoading] = useState(true)
  const [textContent, setTextContent] = useState(null)
  const [error, setError] = useState(null)
  const [fileUrl, setFileUrl] = useState(null)

  const previewType = getPreviewType(file?.extension)
  const currentIndex = files.findIndex(f => f.id === file?.id)
  const canGoPrev = currentIndex > 0
  const canGoNext = currentIndex < files.length - 1

  useEffect(() => {
    if (isOpen && file) {
      setLoading(true)
      setTextContent(null)
      setError(null)

      // For code and text files, fetch content
      if (previewType === 'code' || previewType === 'text') {
        let cancelled = false
        fetchFileText(file.id)
          .then(text => {
            if (cancelled) return
            setTextContent(text)
            setLoading(false)
          })
          .catch(() => {
            if (cancelled) return
            setError('Failed to load file')
            setLoading(false)
          })
        return () => { cancelled = true }
      } else {
        setLoading(false)
      }
    }
  }, [isOpen, file?.id, previewType])

  useEffect(() => {
    if (!isOpen || !file || previewType === 'code' || previewType === 'text') {
      return undefined
    }
    let revoked = false
    let created = null
    fetchFileObjectUrl(file.id)
      .then(url => {
        if (revoked) { URL.revokeObjectURL(url); return }
        created = url
        setFileUrl(url)
      })
      .catch(() => {
        // Same late-resolution guard as the `then` path above (and the text
        // effect): without it, a slow file A that rejects AFTER the user has
        // navigated to file B paints "Failed to load file" over B's working
        // preview, attributing A's failure to B.
        if (revoked) return
        setError('Failed to load file')
      })
    return () => {
      revoked = true
      setFileUrl(null)
      if (created) URL.revokeObjectURL(created)
    }
  }, [isOpen, file?.id, previewType])

  useEffect(() => {
    const handleKeyDown = (e) => {
      if (!isOpen) return

      if (e.key === 'Escape') {
        onClose()
      } else if (e.key === 'ArrowLeft' && canGoPrev && onNavigate) {
        onNavigate(files[currentIndex - 1])
      } else if (e.key === 'ArrowRight' && canGoNext && onNavigate) {
        onNavigate(files[currentIndex + 1])
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [isOpen, onClose, canGoPrev, canGoNext, currentIndex, files, onNavigate])

  if (!isOpen || !file) return null

  const handleDownload = () => {
    downloadFileBlob(file.id, file.name).catch(() =>
      setError('Failed to download file')
    )
  }

  const handlePrev = () => {
    if (canGoPrev && onNavigate) {
      onNavigate(files[currentIndex - 1])
    }
  }

  const handleNext = () => {
    if (canGoNext && onNavigate) {
      onNavigate(files[currentIndex + 1])
    }
  }

  const renderPreview = () => {
    if (loading) {
      return (
        <div className="flex-1 flex items-center justify-center bg-gray-900">
          <Loader2 className="h-12 w-12 text-gray-400 animate-spin" />
        </div>
      )
    }

    if (error) {
      return (
        <div className="flex-1 flex flex-col items-center justify-center bg-gray-900">
          <p className="text-red-400 mb-4">{error}</p>
          <button
            onClick={handleDownload}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
          >
            Download Instead
          </button>
        </div>
      )
    }

    switch (previewType) {
      case 'image':
        return <ImagePreview src={fileUrl} alt={file.name} />
      case 'video':
        return <VideoPreview src={fileUrl} />
      case 'audio':
        return <AudioPreview src={fileUrl} fileName={file.name} />
      case 'pdf':
        return <PdfPreview src={fileUrl} />
      case 'code':
        return <CodePreview content={textContent} extension={file.extension} fileName={file.name} />
      case 'text':
        return <TextPreview content={textContent} fileName={file.name} />
      default:
        return <UnsupportedPreview file={file} />
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-black/95">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 bg-gray-900">
        <div className="flex items-center gap-4">
          <button
            onClick={onClose}
            className="p-2 text-gray-300 hover:bg-gray-700 rounded-lg"
          >
            <X className="h-5 w-5" />
          </button>
          <div>
            <h3 className="text-white font-medium truncate max-w-md">{file.name}</h3>
            <p className="text-gray-400 text-sm">{file.size_human}</p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {onShare && (
            <button
              onClick={() => onShare(file)}
              className="flex items-center gap-2 px-3 py-2 text-gray-300 hover:bg-gray-700 rounded-lg"
            >
              <Share2 className="h-4 w-4" />
              <span className="hidden sm:inline">Share</span>
            </button>
          )}
          <button
            onClick={handleDownload}
            className="flex items-center gap-2 px-3 py-2 text-gray-300 hover:bg-gray-700 rounded-lg"
          >
            <Download className="h-4 w-4" />
            <span className="hidden sm:inline">Download</span>
          </button>
        </div>
      </div>

      {/* Preview Area */}
      <div className="flex-1 flex overflow-hidden relative">
        {/* Navigation arrows */}
        {files.length > 1 && (
          <>
            <button
              onClick={handlePrev}
              disabled={!canGoPrev}
              className={`absolute left-4 top-1/2 -translate-y-1/2 p-3 rounded-full bg-black/50 text-white z-10 ${
                canGoPrev ? 'hover:bg-black/70' : 'opacity-30 cursor-not-allowed'
              }`}
            >
              <ChevronLeft className="h-6 w-6" />
            </button>
            <button
              onClick={handleNext}
              disabled={!canGoNext}
              className={`absolute right-4 top-1/2 -translate-y-1/2 p-3 rounded-full bg-black/50 text-white z-10 ${
                canGoNext ? 'hover:bg-black/70' : 'opacity-30 cursor-not-allowed'
              }`}
            >
              <ChevronRight className="h-6 w-6" />
            </button>
          </>
        )}

        {renderPreview()}
      </div>

      {/* Footer with file info */}
      {files.length > 1 && (
        <div className="flex items-center justify-center py-2 bg-gray-900 text-gray-400 text-sm">
          {currentIndex + 1} of {files.length}
        </div>
      )}
    </div>
  )
}
