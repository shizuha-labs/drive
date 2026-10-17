import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Star, File, FileText, Image, Film, Music, Archive, Code } from 'lucide-react'
import { getStarredFiles, toggleStar } from '../services/api'
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

export default function Starred() {
  const queryClient = useQueryClient()

  const { data: files, isLoading } = useQuery({
    queryKey: ['starred-files'],
    queryFn: getStarredFiles,
  })

  const starMutation = useMutation({
    mutationFn: toggleStar,
    onSuccess: () => {
      queryClient.invalidateQueries(['starred-files'])
    },
  })

  if (isLoading) {
    return (
      <div className="flex justify-center items-center h-64">
        <LoadingSpinner size="lg" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold text-gray-900 flex items-center">
        <Star className="mr-2 h-6 w-6 text-yellow-500" />
        Starred
      </h1>

      <div className="bg-white rounded-lg shadow-sm border border-gray-200">
        {files && files.length > 0 ? (
          <div className="divide-y divide-gray-200">
            {files.map((file) => {
              const Icon = fileTypeIcons[file.file_type] || File
              return (
                <div
                  key={file.id}
                  className="flex items-center justify-between p-4 hover:bg-gray-50"
                >
                  <div className="flex items-center space-x-3">
                    {file.has_thumbnail && file.thumbnail_url ? (
                      <img
                        src={file.thumbnail_url}
                        alt={file.name}
                        className="h-10 w-10 object-cover rounded"
                      />
                    ) : (
                      <Icon className="h-10 w-10 text-gray-400" />
                    )}
                    <div>
                      <div className="text-sm font-medium text-gray-900">
                        {file.name}
                      </div>
                      <div className="text-xs text-gray-500">
                        {file.size_human} · Modified{' '}
                        {formatDistanceToNow(new Date(file.updated_at), {
                          addSuffix: true,
                        })}
                      </div>
                    </div>
                  </div>
                  <button
                    onClick={() => starMutation.mutate(file.id)}
                    className="p-2 rounded-full hover:bg-gray-100"
                  >
                    <Star className="h-5 w-5 text-yellow-500 fill-current" />
                  </button>
                </div>
              )
            })}
          </div>
        ) : (
          <div className="text-center py-16">
            <Star className="h-16 w-16 text-gray-300 mx-auto mb-4" />
            <h3 className="text-lg font-medium text-gray-900 mb-2">
              No starred files
            </h3>
            <p className="text-gray-500">
              Star files to quickly access them later
            </p>
          </div>
        )}
      </div>
    </div>
  )
}
