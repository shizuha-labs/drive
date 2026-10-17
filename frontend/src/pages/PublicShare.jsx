import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { File, FileText, Image, Film, Music, Archive, Code, Download, Lock, AlertTriangle } from 'lucide-react'
import api from '../services/api'
import { getPublicShare } from '../services/api'
import LoadingSpinner from '../components/LoadingSpinner'
import { useState } from 'react'

// DRV-184: the public share API (DRV-12 contract) returns a FLAT payload —
// `{type: 'file'|'folder', name, size_human, mime_type, allow_download}` —
// with no nested `{file, folder, share}` wrapper, no `download_url` (bytes
// are gated behind POST action='download'), and no `file_type` on file
// shares (derive the icon from mime_type). The previous implementation
// destructured `{file, folder, share}` and threw on `f.file_type`, blanking
// the page for every valid share link.

const iconForMime = (mime = '') => {
  const m = mime.toLowerCase()
  if (m.startsWith('image/')) return Image
  if (m.startsWith('video/')) return Film
  if (m.startsWith('audio/')) return Music
  if (m.includes('zip') || m.includes('tar') || m.includes('compressed') || m.includes('gzip')) return Archive
  if (m.includes('json') || m.includes('javascript') || m.includes('python') || m.includes('yaml')) return Code
  if (m.startsWith('text/') || m.includes('pdf') || m.includes('word') || m.includes('sheet') || m.includes('presentation')) return FileText
  return File
}

export default function PublicShare() {
  const { token } = useParams()
  const [password, setPassword] = useState('')
  const [passwordError, setPasswordError] = useState(false)
  const [submittedPassword, setSubmittedPassword] = useState(null)
  const [downloading, setDownloading] = useState(false)
  const [downloadError, setDownloadError] = useState(false)

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['public-share', token, submittedPassword],
    queryFn: () => getPublicShare(token, submittedPassword),
    retry: false,
  })

  const handlePasswordSubmit = (e) => {
    e.preventDefault()
    setPasswordError(false)
    setSubmittedPassword(password)
    refetch()
  }

  // DRV-12: file bytes are gated behind POST action='download' (allow_download
  // enforced server-side); there is no static download_url in the metadata.
  const handleDownload = async () => {
    setDownloading(true)
    setDownloadError(false)
    try {
      const resp = await api.post(
        `/public/${token}/`,
        { action: 'download', password: submittedPassword || undefined },
        { responseType: 'blob' },
      )
      const url = URL.createObjectURL(resp.data)
      const a = document.createElement('a')
      a.href = url
      a.download = data?.name || 'download'
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch {
      setDownloadError(true)
    } finally {
      setDownloading(false)
    }
  }

  if (isLoading) {
    return (
      <div className="min-h-screen bg-gray-100 flex items-center justify-center">
        <LoadingSpinner size="lg" />
      </div>
    )
  }

  // Password required
  if (error?.response?.status === 401 || data?.requires_password) {
    return (
      <div className="min-h-screen bg-gray-100 flex items-center justify-center p-4">
        <div className="bg-white rounded-lg shadow-lg p-8 max-w-md w-full">
          <div className="text-center mb-6">
            <Lock className="h-12 w-12 text-gray-400 mx-auto mb-4" />
            <h1 className="text-xl font-semibold text-gray-900">
              Password Protected
            </h1>
            <p className="text-gray-500 mt-2">
              This file is password protected. Enter the password to access it.
            </p>
          </div>
          <form onSubmit={handlePasswordSubmit}>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Enter password"
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
            {passwordError && (
              <p className="text-red-500 text-sm mt-2">Incorrect password</p>
            )}
            <button
              type="submit"
              className="w-full mt-4 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
            >
              Access File
            </button>
          </form>
        </div>
      </div>
    )
  }

  // Error or not found
  if (error || !data) {
    return (
      <div className="min-h-screen bg-gray-100 flex items-center justify-center p-4">
        <div className="bg-white rounded-lg shadow-lg p-8 max-w-md w-full text-center">
          <AlertTriangle className="h-12 w-12 text-red-400 mx-auto mb-4" />
          <h1 className="text-xl font-semibold text-gray-900">
            Link Not Found
          </h1>
          <p className="text-gray-500 mt-2">
            This share link may have expired, been revoked, or never existed.
          </p>
        </div>
      </div>
    )
  }

  const isFolder = data.type === 'folder'

  if (isFolder) {
    // Folder view — flat payload: {type:'folder', name, files[], allow_download}.
    // Per-file download is not offered: the backend gates bytes at the share
    // level and the download branch handles share.file only.
    return (
      <div className="min-h-screen bg-gray-100 p-4">
        <div className="max-w-4xl mx-auto">
          <div className="bg-white rounded-lg shadow-lg p-6">
            <h1 className="text-2xl font-semibold text-gray-900 mb-6">
              {data.name}
            </h1>

            {data.files && data.files.length > 0 ? (
              <div className="divide-y divide-gray-200">
                {data.files.map((f) => {
                  const Icon = iconForMime(f.mime_type)
                  return (
                    <div
                      key={f.id}
                      className="flex items-center justify-between py-4"
                    >
                      <div className="flex items-center space-x-3">
                        {f.has_thumbnail && f.thumbnail_url ? (
                          <img
                            src={f.thumbnail_url}
                            alt={f.name}
                            className="h-10 w-10 object-cover rounded"
                          />
                        ) : (
                          <Icon className="h-10 w-10 text-gray-400" />
                        )}
                        <div>
                          <div className="text-sm font-medium text-gray-900">
                            {f.name}
                          </div>
                          <div className="text-xs text-gray-500">
                            {f.size_human}
                          </div>
                        </div>
                      </div>
                    </div>
                  )
                })}
              </div>
            ) : (
              <p className="text-gray-500 text-center py-8">
                This folder is empty
              </p>
            )}
          </div>
        </div>

        <p className="text-center text-sm text-gray-500 mt-6">
          Shared via Shizuha Drive
        </p>
      </div>
    )
  }

  // File view — flat payload: {type:'file', name, size_human, mime_type, allow_download}.
  const Icon = iconForMime(data.mime_type)

  return (
    <div className="min-h-screen bg-gray-100 p-4">
      <div className="max-w-4xl mx-auto">
        <div className="bg-white rounded-lg shadow-lg overflow-hidden">
          {/* Preview area — the flat metadata contract exposes no preview_url;
              the type icon represents the file. */}
          <div className="bg-gray-900 flex items-center justify-center min-h-[300px]">
            <Icon className="h-24 w-24 text-gray-400" />
          </div>

          {/* File info */}
          <div className="p-6">
            <div className="flex items-center justify-between">
              <div>
                <h1 className="text-xl font-semibold text-gray-900">
                  {data.name}
                </h1>
                <p className="text-sm text-gray-500 mt-1">
                  {data.size_human} · {data.mime_type}
                </p>
              </div>
              {data.allow_download && (
                <button
                  onClick={handleDownload}
                  disabled={downloading}
                  className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 flex items-center disabled:opacity-50"
                >
                  <Download className="h-4 w-4 mr-2" />
                  {downloading ? 'Downloading…' : 'Download'}
                </button>
              )}
            </div>
            {downloadError && (
              <p className="text-red-500 text-sm mt-2">
                Download failed — try again.
              </p>
            )}
          </div>
        </div>

        <p className="text-center text-sm text-gray-500 mt-6">
          Shared via Shizuha Drive
        </p>
      </div>
    </div>
  )
}
