import { useState, useEffect } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  X,
  Share2,
  Link2,
  Copy,
  Check,
  Loader2,
  Calendar,
  Lock,
  Globe,
  Trash2,
  Users,
} from 'lucide-react'
import { createShare, getShares, revokeShare } from '../services/api'
import { useToast } from './Toast'

export default function ShareDialog({
  isOpen,
  onClose,
  item,
  itemType = 'file', // 'file' | 'folder'
}) {
  const [activeTab, setActiveTab] = useState('link')
  const [linkCopied, setLinkCopied] = useState(false)
  const [showPassword, setShowPassword] = useState(false)
  const [password, setPassword] = useState('')
  const [expiresAt, setExpiresAt] = useState('')
  // DRV-80: "Share with People" — recipient email + permission for user shares.
  const [recipientEmail, setRecipientEmail] = useState('')
  const [recipientPermission, setRecipientPermission] = useState('view')
  const queryClient = useQueryClient()
  const toast = useToast()

  // Fetch existing shares for this item
  const { data: shares, isLoading: sharesLoading } = useQuery({
    queryKey: ['shares', itemType, item?.id],
    queryFn: getShares,
    enabled: isOpen && !!item,
    select: (data) => data.results?.filter(
      s => (itemType === 'file' ? s.file === item?.id : s.folder === item?.id)
    ) || []
  })

  // Create share mutation
  const createShareMutation = useMutation({
    mutationFn: createShare,
    onSuccess: (data) => {
      queryClient.invalidateQueries(['shares'])
      toast.success('Share link created')
      // Copy to clipboard
      const shareUrl = `${window.location.origin}/drive/share/${data.link_token}`
      navigator.clipboard.writeText(shareUrl)
      setLinkCopied(true)
      setTimeout(() => setLinkCopied(false), 2000)
    },
    onError: (error) => {
      toast.error(error.response?.data?.detail || 'Failed to create share link')
    },
  })

  // Revoke share mutation
  const revokeShareMutation = useMutation({
    mutationFn: revokeShare,
    onSuccess: () => {
      queryClient.invalidateQueries(['shares'])
      toast.success('Share link revoked')
    },
    onError: (error) => {
      toast.error(error.response?.data?.detail || 'Failed to revoke share')
    },
  })

  useEffect(() => {
    if (isOpen) {
      setActiveTab('link')
      setPassword('')
      setExpiresAt('')
      setShowPassword(false)
      setLinkCopied(false)
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

  const handleCreateLink = () => {
    const shareData = {
      share_type: 'link',
      permission: 'view',
    }

    if (itemType === 'file') {
      shareData.file = item.id
    } else {
      shareData.folder = item.id
    }

    if (showPassword && password) {
      shareData.password = password
    }

    if (expiresAt) {
      shareData.expires_at = new Date(expiresAt).toISOString()
    }

    createShareMutation.mutate(shareData)
  }

  const handleCopyLink = (linkToken) => {
    const shareUrl = `${window.location.origin}/drive/share/${linkToken}`
    navigator.clipboard.writeText(shareUrl)
    setLinkCopied(true)
    toast.success('Link copied to clipboard')
    setTimeout(() => setLinkCopied(false), 2000)
  }

  const handleRevokeShare = (shareId) => {
    revokeShareMutation.mutate(shareId)
  }

  // DRV-80: create a user share (specific recipient + explicit permission).
  const handleCreateUserShare = () => {
    const email = recipientEmail.trim()
    if (!email) {
      toast.error('Enter the recipient email')
      return
    }
    const shareData = {
      share_type: 'user',
      permission: recipientPermission,
      shared_with_email: email,
    }
    if (itemType === 'file') {
      shareData.file = item.id
    } else {
      shareData.folder = item.id
    }
    createShareMutation.mutate(shareData, {
      onSuccess: () => {
        setRecipientEmail('')
        setRecipientPermission('view')
      },
    })
  }

  if (!isOpen) return null

  const existingLinks = shares?.filter(s => s.share_type === 'link' && s.is_active) || []
  const existingPeople = shares?.filter(s => s.share_type === 'user' && s.is_active) || []

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />

      {/* Dialog */}
      <div className="relative bg-white rounded-lg shadow-xl max-w-lg w-full mx-4 animate-scale-in flex flex-col max-h-[80vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b flex-shrink-0">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-blue-100 rounded-lg">
              <Share2 className="h-5 w-5 text-blue-600" />
            </div>
            <div>
              <h3 className="text-lg font-semibold text-gray-900">
                Share "{item?.name}"
              </h3>
              <p className="text-sm text-gray-500">Create a shareable link</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded-lg hover:bg-gray-100 text-gray-400"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Tabs */}
        <div className="flex border-b px-6">
          <button
            onClick={() => setActiveTab('link')}
            className={`px-4 py-3 text-sm font-medium border-b-2 -mb-px ${
              activeTab === 'link'
                ? 'border-blue-600 text-blue-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            <div className="flex items-center gap-2">
              <Link2 className="h-4 w-4" />
              Get Link
            </div>
          </button>
          <button
            onClick={() => setActiveTab('people')}
            className={`px-4 py-3 text-sm font-medium border-b-2 -mb-px ${
              activeTab === 'people'
                ? 'border-blue-600 text-blue-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            <div className="flex items-center gap-2">
              <Users className="h-4 w-4" />
              Share with People
            </div>
          </button>
        </div>

        {/* Content */}
        <div className="p-6 flex-1 overflow-auto">
          {activeTab === 'link' && (
            <div className="space-y-4">
              {/* Existing links */}
              {existingLinks.length > 0 && (
                <div className="mb-6">
                  <h4 className="text-sm font-medium text-gray-700 mb-3">Active Links</h4>
                  <div className="space-y-2">
                    {existingLinks.map((share) => (
                      <div
                        key={share.id}
                        className="flex items-center justify-between p-3 bg-gray-50 rounded-lg"
                      >
                        <div className="flex items-center gap-3">
                          <Globe className="h-5 w-5 text-green-500" />
                          <div>
                            <p className="text-sm font-medium text-gray-900">
                              Anyone with the link
                            </p>
                            <p className="text-xs text-gray-500">
                              {share.has_password && '🔒 Password protected • '}
                              {share.expires_at
                                ? `Expires ${new Date(share.expires_at).toLocaleDateString()}`
                                : 'No expiration'}
                            </p>
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          <button
                            onClick={() => handleCopyLink(share.link_token)}
                            className="p-2 text-gray-500 hover:bg-gray-200 rounded-lg"
                            title="Copy link"
                          >
                            {linkCopied ? (
                              <Check className="h-4 w-4 text-green-500" />
                            ) : (
                              <Copy className="h-4 w-4" />
                            )}
                          </button>
                          <button
                            onClick={() => handleRevokeShare(share.id)}
                            className="p-2 text-red-500 hover:bg-red-50 rounded-lg"
                            title="Revoke link"
                            disabled={revokeShareMutation.isLoading}
                          >
                            <Trash2 className="h-4 w-4" />
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Create new link */}
              <div className="space-y-4">
                <h4 className="text-sm font-medium text-gray-700">
                  {existingLinks.length > 0 ? 'Create Another Link' : 'Create Share Link'}
                </h4>

                {/* Password protection */}
                <div className="flex items-center justify-between p-3 border rounded-lg">
                  <div className="flex items-center gap-3">
                    <Lock className="h-5 w-5 text-gray-400" />
                    <div>
                      <p className="text-sm font-medium text-gray-900">Password protection</p>
                      <p className="text-xs text-gray-500">Require a password to access</p>
                    </div>
                  </div>
                  <label className="relative inline-flex items-center cursor-pointer">
                    <input
                      type="checkbox"
                      checked={showPassword}
                      onChange={(e) => setShowPassword(e.target.checked)}
                      className="sr-only peer"
                    />
                    <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-blue-300 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-blue-600"></div>
                  </label>
                </div>

                {showPassword && (
                  <input
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="Enter password"
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  />
                )}

                {/* Expiration */}
                <div className="flex items-center gap-3 p-3 border rounded-lg">
                  <Calendar className="h-5 w-5 text-gray-400" />
                  <div className="flex-1">
                    <p className="text-sm font-medium text-gray-900 mb-1">Expiration date</p>
                    <input
                      type="datetime-local"
                      value={expiresAt}
                      onChange={(e) => setExpiresAt(e.target.value)}
                      min={new Date().toISOString().slice(0, 16)}
                      className="w-full px-2 py-1 text-sm border border-gray-200 rounded focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                    />
                  </div>
                </div>

                <button
                  onClick={handleCreateLink}
                  disabled={createShareMutation.isLoading}
                  className="w-full flex items-center justify-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
                >
                  {createShareMutation.isLoading ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Link2 className="h-4 w-4" />
                  )}
                  Create Link
                </button>
              </div>
            </div>
          )}

          {activeTab === 'people' && (
            <div className="space-y-4">
              {/* Recipient + permission entry (DRV-80) */}
              <div className="flex items-center gap-2">
                <input
                  type="email"
                  value={recipientEmail}
                  onChange={(e) => setRecipientEmail(e.target.value)}
                  placeholder="Recipient email"
                  className="flex-1 px-2 py-1 text-sm border border-gray-200 rounded focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                />
                <select
                  value={recipientPermission}
                  onChange={(e) => setRecipientPermission(e.target.value)}
                  className="px-2 py-1 text-sm border border-gray-200 rounded focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  aria-label="Permission"
                >
                  <option value="view">Can view</option>
                  <option value="edit">Can edit</option>
                </select>
                <button
                  onClick={handleCreateUserShare}
                  disabled={createShareMutation.isLoading}
                  className="flex items-center gap-1 px-3 py-1 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700 disabled:opacity-50"
                >
                  {createShareMutation.isLoading ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Users className="h-4 w-4" />
                  )}
                  Share
                </button>
              </div>

              {/* Existing user shares with revoke */}
              {existingPeople.length > 0 && (
                <div className="space-y-2">
                  <p className="text-sm font-medium text-gray-900">Shared with</p>
                  {existingPeople.map((s) => (
                    <div key={s.id} className="flex items-center justify-between p-3 border rounded-lg">
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-gray-900 truncate">
                          {s.shared_with_email || `User ${s.shared_with_id}`}
                        </p>
                        <p className="text-xs text-gray-500">
                          {s.permission === 'edit' ? 'Can edit' : 'Can view'}
                        </p>
                      </div>
                      <button
                        onClick={() => handleRevokeShare(s.id)}
                        disabled={revokeShareMutation.isLoading}
                        className="p-1.5 text-gray-400 hover:text-red-600 rounded hover:bg-red-50 disabled:opacity-50"
                        aria-label={`Revoke share for ${s.shared_with_email || s.shared_with_id}`}
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                  ))}
                </div>
              )}
              {existingPeople.length === 0 && (
                <p className="text-sm text-gray-500">
                  No one has access yet. Enter a recipient email and choose a permission to share.
                </p>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-end px-6 py-4 border-t flex-shrink-0">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200"
          >
            Done
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
