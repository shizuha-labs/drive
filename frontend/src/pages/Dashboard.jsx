import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import {
  File,
  Folder,
  Image,
  FileText,
  Film,
  Music,
  Archive,
  Code,
  HardDrive,
  Clock,
  TrendingUp,
} from 'lucide-react'
import { getDashboard } from '../services/api'
import LoadingSpinner from '../components/LoadingSpinner'
import { formatDistanceToNow } from 'date-fns'

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

const fileTypeColors = {
  document: 'text-blue-500 bg-blue-50',
  spreadsheet: 'text-green-500 bg-green-50',
  presentation: 'text-orange-500 bg-orange-50',
  image: 'text-purple-500 bg-purple-50',
  video: 'text-red-500 bg-red-50',
  audio: 'text-pink-500 bg-pink-50',
  archive: 'text-yellow-600 bg-yellow-50',
  code: 'text-gray-600 bg-gray-50',
  other: 'text-gray-500 bg-gray-50',
}

function formatBytes(bytes) {
  if (!bytes) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${(bytes / Math.pow(k, i)).toFixed(1)} ${sizes[i]}`
}

export default function Dashboard() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['dashboard'],
    queryFn: getDashboard,
  })

  if (isLoading) {
    return (
      <div className="flex justify-center items-center h-64">
        <LoadingSpinner size="lg" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="text-center text-red-500 py-8">
        Failed to load dashboard data
      </div>
    )
  }

  const { quota, file_stats, recent_files, recent_activity } = data
  const totalUsagePercent = Number(quota.total_usage_percent ?? quota.usage_percent ?? 0)
  const totalUsedHuman = quota.total_used_human || quota.used_human

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold text-gray-900">Dashboard</h1>

      {/* Storage Overview */}
      <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-medium text-gray-900 flex items-center">
            <HardDrive className="mr-2 h-5 w-5 text-blue-600" />
            Storage Overview
          </h2>
          <Link to="/settings" className="text-sm text-blue-600 hover:underline">
            Manage storage
          </Link>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {/* Usage Bar */}
          <div className="col-span-2">
            <div className="mb-2 flex justify-between text-sm">
              <span className="text-gray-600">
                {totalUsedHuman} of {quota.quota_human} used
              </span>
              <span className="text-gray-500">{totalUsagePercent.toFixed(1)}%</span>
            </div>
            <div className="w-full bg-gray-200 rounded-full h-3">
              <div
                className={`h-3 rounded-full transition-all ${
                  totalUsagePercent >= 90
                    ? 'bg-red-500'
                    : totalUsagePercent > 70
                    ? 'bg-yellow-500'
                    : 'bg-blue-600'
                }`}
                style={{ width: `${Math.min(totalUsagePercent, 100)}%` }}
              />
            </div>
            {totalUsagePercent >= 90 && (
              <p className="mt-2 text-sm font-medium text-red-600">
                {totalUsagePercent >= 100
                  ? 'Storage is full. Uploads are blocked.'
                  : 'Storage is nearly full.'}{' '}
                Usage includes Drive and connected Shizuha services.
              </p>
            )}
          </div>

          {/* Stats */}
          <div className="flex justify-around">
            <div className="text-center">
              <div className="text-2xl font-semibold text-gray-900">
                {quota.file_count}
              </div>
              <div className="text-sm text-gray-500">Files</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-semibold text-gray-900">
                {quota.folder_count}
              </div>
              <div className="text-sm text-gray-500">Folders</div>
            </div>
          </div>
        </div>
      </div>

      {/* File Types Breakdown */}
      {file_stats && file_stats.length > 0 && (
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
          <h2 className="text-lg font-medium text-gray-900 flex items-center mb-4">
            <TrendingUp className="mr-2 h-5 w-5 text-green-600" />
            Storage by File Type
          </h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {file_stats.map((stat) => {
              const Icon = fileTypeIcons[stat.file_type] || File
              const colors = fileTypeColors[stat.file_type] || fileTypeColors.other
              return (
                <div
                  key={stat.file_type}
                  className="flex items-center space-x-3 p-3 rounded-lg bg-gray-50"
                >
                  <div className={`p-2 rounded-lg ${colors}`}>
                    <Icon size={20} />
                  </div>
                  <div>
                    <div className="text-sm font-medium text-gray-900 capitalize">
                      {stat.file_type}
                    </div>
                    <div className="text-xs text-gray-500">
                      {stat.count} files · {formatBytes(stat.total_size)}
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Recent Files */}
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-medium text-gray-900 flex items-center">
              <Clock className="mr-2 h-5 w-5 text-gray-600" />
              Recent Files
            </h2>
            <Link to="/my-drive" className="text-sm text-blue-600 hover:underline">
              View all
            </Link>
          </div>

          {recent_files && recent_files.length > 0 ? (
            <div className="space-y-3">
              {recent_files.map((file) => {
                const Icon = fileTypeIcons[file.file_type] || File
                return (
                  <div
                    key={file.id}
                    className="flex items-center space-x-3 p-2 rounded-lg hover:bg-gray-50"
                  >
                    <Icon className="h-8 w-8 text-gray-400" />
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-medium text-gray-900 truncate">
                        {file.name}
                      </div>
                      <div className="text-xs text-gray-500">
                        {file.size_human} ·{' '}
                        {formatDistanceToNow(new Date(file.updated_at), {
                          addSuffix: true,
                        })}
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          ) : (
            <div className="text-center text-gray-500 py-8">
              No files yet. Start by uploading some files!
            </div>
          )}
        </div>

        {/* Recent Activity */}
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
          <h2 className="text-lg font-medium text-gray-900 mb-4">
            Recent Activity
          </h2>

          {recent_activity && recent_activity.length > 0 ? (
            <div className="space-y-3">
              {recent_activity.map((activity) => (
                <div
                  key={activity.id}
                  className="flex items-start space-x-3 py-2 border-b border-gray-100 last:border-0"
                >
                  <div className="w-2 h-2 mt-2 rounded-full bg-blue-500" />
                  <div className="flex-1">
                    <div className="text-sm text-gray-900">
                      <span className="capitalize">{activity.action}</span>{' '}
                      <span className="font-medium">{activity.target_name}</span>
                    </div>
                    <div className="text-xs text-gray-500">
                      {formatDistanceToNow(new Date(activity.created_at), {
                        addSuffix: true,
                      })}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-center text-gray-500 py-8">
              No activity yet
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
