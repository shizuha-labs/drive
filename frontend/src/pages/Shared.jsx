import { useQuery } from '@tanstack/react-query'
import { Users, File, Folder, Clock } from 'lucide-react'
import { getShares } from '../services/api'
import LoadingSpinner from '../components/LoadingSpinner'
import { formatDistanceToNow } from 'date-fns'

export default function Shared() {
  const { data: shares, isLoading } = useQuery({
    queryKey: ['shares'],
    queryFn: getShares,
  })

  if (isLoading) {
    return (
      <div className="flex justify-center items-center h-64">
        <LoadingSpinner size="lg" />
      </div>
    )
  }

  const sharedWithMe = shares?.results?.filter(s => s.created_by_id !== s.shared_with_id) || []
  const sharedByMe = shares?.results?.filter(s => s.created_by_id === s.shared_with_id) || []

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold text-gray-900">Shared</h1>

      {/* Shared with me */}
      <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
        <h2 className="text-lg font-medium text-gray-900 flex items-center mb-4">
          <Users className="mr-2 h-5 w-5 text-blue-600" />
          Shared with me
        </h2>

        {sharedWithMe.length > 0 ? (
          <div className="space-y-3">
            {sharedWithMe.map((share) => (
              <div
                key={share.id}
                className="flex items-center justify-between p-3 rounded-lg hover:bg-gray-50"
              >
                <div className="flex items-center space-x-3">
                  {share.target_type === 'file' ? (
                    <File className="h-8 w-8 text-gray-400" />
                  ) : (
                    <Folder className="h-8 w-8 text-yellow-500" fill="currentColor" />
                  )}
                  <div>
                    <div className="text-sm font-medium text-gray-900">
                      {share.target_name}
                    </div>
                    <div className="text-xs text-gray-500">
                      {share.permission} access
                    </div>
                  </div>
                </div>
                <div className="text-xs text-gray-500">
                  {formatDistanceToNow(new Date(share.created_at), { addSuffix: true })}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-center text-gray-500 py-8">
            No files shared with you yet
          </div>
        )}
      </div>

      {/* Shared by me */}
      <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
        <h2 className="text-lg font-medium text-gray-900 mb-4">Shared by me</h2>

        {sharedByMe.length > 0 ? (
          <div className="space-y-3">
            {sharedByMe.map((share) => (
              <div
                key={share.id}
                className="flex items-center justify-between p-3 rounded-lg hover:bg-gray-50"
              >
                <div className="flex items-center space-x-3">
                  {share.target_type === 'file' ? (
                    <File className="h-8 w-8 text-gray-400" />
                  ) : (
                    <Folder className="h-8 w-8 text-yellow-500" fill="currentColor" />
                  )}
                  <div>
                    <div className="text-sm font-medium text-gray-900">
                      {share.target_name}
                    </div>
                    <div className="text-xs text-gray-500">
                      {share.share_type === 'link' ? 'Link share' : `Shared with ${share.shared_with_email}`}
                    </div>
                  </div>
                </div>
                <div className="flex items-center space-x-2">
                  <span className="text-xs text-gray-500">
                    {share.access_count} views
                  </span>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-center text-gray-500 py-8">
            You haven't shared any files yet
          </div>
        )}
      </div>
    </div>
  )
}
