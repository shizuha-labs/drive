import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Settings as SettingsIcon, HardDrive, Trash2, RefreshCw } from 'lucide-react'
import { getStorageQuota, emptyTrash } from '../services/api'
import LoadingSpinner from '../components/LoadingSpinner'
import { useState } from 'react'

function formatBytes(bytes) {
  if (bytes === 0) return '0 Bytes'
  const k = 1024
  const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
}

export default function Settings() {
  const queryClient = useQueryClient()
  const [emptying, setEmptying] = useState(false)

  const { data: quota, isLoading } = useQuery({
    queryKey: ['quota'],
    queryFn: getStorageQuota,
  })

  const emptyTrashMutation = useMutation({
    mutationFn: emptyTrash,
    onSuccess: () => {
      queryClient.invalidateQueries(['trash'])
      queryClient.invalidateQueries(['quota'])
      setEmptying(false)
    },
    onError: () => {
      setEmptying(false)
    },
  })

  const handleEmptyTrash = () => {
    if (window.confirm('Permanently delete all files in trash? This cannot be undone.')) {
      setEmptying(true)
      emptyTrashMutation.mutate()
    }
  }

  if (isLoading) {
    return (
      <div className="flex justify-center items-center h-64">
        <LoadingSpinner size="lg" />
      </div>
    )
  }

  const usedPercentage = Number(quota?.total_usage_percent ?? quota?.usage_percent ?? 0)
  const totalUsedBytes = quota?.total_used_bytes ?? quota?.used_bytes ?? 0

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold text-gray-900 flex items-center">
        <SettingsIcon className="mr-2 h-6 w-6 text-gray-500" />
        Settings
      </h1>

      <div className="grid gap-6">
        {/* Storage Section */}
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
          <h2 className="text-lg font-medium text-gray-900 flex items-center mb-4">
            <HardDrive className="mr-2 h-5 w-5 text-gray-500" />
            Storage
          </h2>

          {quota ? (
            <div className="space-y-4">
              <div>
                <div className="flex justify-between text-sm mb-2">
                  <span className="text-gray-600">
                    {formatBytes(totalUsedBytes)} of {formatBytes(quota.quota_bytes)} used
                  </span>
                  <span className="text-gray-500">{usedPercentage.toFixed(1)}%</span>
                </div>
                <div className="w-full bg-gray-200 rounded-full h-3">
                  <div
                    className={`h-3 rounded-full ${
                      usedPercentage >= 90
                        ? 'bg-red-500'
                        : usedPercentage > 75
                        ? 'bg-yellow-500'
                        : 'bg-blue-500'
                    }`}
                    style={{ width: `${Math.min(usedPercentage, 100)}%` }}
                  />
                </div>
                <p className={`mt-2 text-sm ${usedPercentage >= 90 ? 'font-medium text-red-600' : 'text-gray-500'}`}>
                  {usedPercentage >= 100
                    ? 'Storage is full. Uploads are blocked. '
                    : usedPercentage >= 90
                    ? 'Storage is nearly full. '
                    : ''}
                  Usage includes Drive and connected Shizuha services.
                </p>
              </div>

              <div className="grid grid-cols-2 gap-4 pt-4 border-t">
                <div>
                  <div className="text-2xl font-semibold text-gray-900">
                    {quota.file_count}
                  </div>
                  <div className="text-sm text-gray-500">Total files</div>
                </div>
                <div>
                  <div className="text-2xl font-semibold text-gray-900">
                    {quota.folder_count}
                  </div>
                  <div className="text-sm text-gray-500">Total folders</div>
                </div>
              </div>
            </div>
          ) : (
            <p className="text-gray-500">Unable to load storage information</p>
          )}
        </div>

        {/* Trash Section */}
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
          <h2 className="text-lg font-medium text-gray-900 flex items-center mb-4">
            <Trash2 className="mr-2 h-5 w-5 text-gray-500" />
            Trash
          </h2>

          <p className="text-sm text-gray-600 mb-4">
            Files in trash are automatically deleted after 30 days. You can empty the trash
            to immediately free up storage space.
          </p>

          <button
            onClick={handleEmptyTrash}
            disabled={emptying}
            className="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 disabled:opacity-50 flex items-center"
          >
            {emptying ? (
              <>
                <RefreshCw className="h-4 w-4 mr-2 animate-spin" />
                Emptying...
              </>
            ) : (
              <>
                <Trash2 className="h-4 w-4 mr-2" />
                Empty Trash
              </>
            )}
          </button>
        </div>

        {/* About Section */}
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
          <h2 className="text-lg font-medium text-gray-900 mb-4">About</h2>
          <div className="space-y-2 text-sm text-gray-600">
            <p>
              <strong>Shizuha Drive</strong> is a secure file storage service that's part of
              the Shizuha platform.
            </p>
            <p>
              Features include file versioning, sharing, collaboration, and automatic
              thumbnail generation for images and documents.
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
