import { useState, useEffect } from 'react'
import { BookOpen, ExternalLink, RefreshCw } from 'lucide-react'

export default function Docs() {
  const [isLoading, setIsLoading] = useState(true)
  const [hasError, setHasError] = useState(false)

  // The docs are served from the backend at /drive/docs/ (nginx proxies to backend)
  const docsUrl = '/drive/docs/index.html'

  const handleIframeLoad = () => {
    setIsLoading(false)
    setHasError(false)
  }

  const handleIframeError = () => {
    setIsLoading(false)
    setHasError(true)
  }

  const openInNewTab = () => {
    window.open(docsUrl, '_blank')
  }

  const refreshDocs = () => {
    setIsLoading(true)
    setHasError(false)
    // Force iframe refresh by updating key
    const iframe = document.getElementById('docs-iframe')
    if (iframe) {
      iframe.src = iframe.src
    }
  }

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b border-gray-200 bg-white">
        <div className="flex items-center gap-3">
          <BookOpen className="w-6 h-6 text-indigo-600" />
          <div>
            <h1 className="text-xl font-semibold text-gray-900">Documentation</h1>
            <p className="text-sm text-gray-500">Shizuha Drive API & Architecture Reference</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={refreshDocs}
            className="p-2 text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
            title="Refresh documentation"
          >
            <RefreshCw className="w-5 h-5" />
          </button>
          <button
            onClick={openInNewTab}
            className="flex items-center gap-2 px-3 py-2 text-sm text-indigo-600 hover:text-indigo-700 hover:bg-indigo-50 rounded-lg transition-colors"
          >
            <ExternalLink className="w-4 h-4" />
            Open in New Tab
          </button>
        </div>
      </div>

      {/* Docs Content */}
      <div className="flex-1 relative bg-gray-50">
        {isLoading && (
          <div className="absolute inset-0 flex items-center justify-center bg-white z-10">
            <div className="flex flex-col items-center gap-3">
              <div className="w-8 h-8 border-4 border-indigo-200 border-t-indigo-600 rounded-full animate-spin" />
              <p className="text-sm text-gray-500">Loading documentation...</p>
            </div>
          </div>
        )}

        {hasError && (
          <div className="absolute inset-0 flex items-center justify-center bg-white z-10">
            <div className="text-center">
              <BookOpen className="w-12 h-12 text-gray-400 mx-auto mb-4" />
              <h2 className="text-lg font-medium text-gray-900 mb-2">Documentation Unavailable</h2>
              <p className="text-sm text-gray-500 mb-4">
                The documentation may still be building or there was an error loading it.
              </p>
              <button
                onClick={refreshDocs}
                className="px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors"
              >
                Try Again
              </button>
            </div>
          </div>
        )}

        <iframe
          id="docs-iframe"
          src={docsUrl}
          className="w-full h-full border-0"
          onLoad={handleIframeLoad}
          onError={handleIframeError}
          title="Shizuha Drive Documentation"
        />
      </div>
    </div>
  )
}
