import { Routes, Route, Navigate } from 'react-router-dom'
import { useAuth } from './contexts/AuthContext'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import MyDrive from './pages/MyDrive'
import Shared from './pages/Shared'
import Starred from './pages/Starred'
import Trash from './pages/Trash'
import Settings from './pages/Settings'
import Docs from './pages/Docs'
import PublicShare from './pages/PublicShare'
import Landing from './pages/Landing'
import Waitlist from './pages/Waitlist'
import LoadingSpinner from './components/LoadingSpinner'
import { ShizuhaChatWidget } from './components/chat'

function LoginRedirect() {
  // Preserve the existing behaviour for app deep-links when unauthenticated:
  // bounce to Shizuha ID login with a continue= back to here.
  const currentUrl = window.location.href
  window.location.href = `/id/login?continue=${encodeURIComponent(currentUrl)}`
  return null
}

function ProtectedRoute({ children }) {
  const { isAuthenticated, isLoading } = useAuth()

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <LoadingSpinner size="lg" />
      </div>
    )
  }

  if (!isAuthenticated) {
    return <LoginRedirect />
  }

  return children
}

export default function App() {
  const { isAuthenticated, isLoading } = useAuth()

  return (
    <>
      <Routes>
        {/* Public share route (no auth required) */}
        <Route path="/share/:token" element={<PublicShare />} />

        {/* DRV-22 (VEN-49): public landing / free-validation waitlist.
            Always reachable, and the default for visitors at the /drive root. */}
        <Route path="/welcome" element={<Landing />} />

        {/* VEN-150: standalone /drive/waitlist page for direct links. */}
        <Route path="/waitlist" element={<Waitlist />} />

        {/* Root and app routes. Authenticated users get the Drive app; visitors
            get the public landing page at the root (NOT a forced login bounce),
            while app deep-links still redirect to login. */}
        <Route
          path="/*"
          element={
            isLoading ? (
              <div className="min-h-screen flex items-center justify-center">
                <LoadingSpinner size="lg" />
              </div>
            ) : isAuthenticated ? (
              <Layout>
                <Routes>
                  <Route path="/" element={<Dashboard />} />
                  <Route path="/my-drive" element={<MyDrive />} />
                  <Route path="/my-drive/:folderId" element={<MyDrive />} />
                  <Route path="/shared" element={<Shared />} />
                  <Route path="/starred" element={<Starred />} />
                  <Route path="/trash" element={<Trash />} />
                  <Route path="/settings" element={<Settings />} />
                  <Route path="/docs" element={<Docs />} />
                  <Route path="*" element={<Navigate to="/" replace />} />
                </Routes>
              </Layout>
            ) : (
              <Routes>
                <Route path="/" element={<Landing />} />
                <Route path="*" element={<LoginRedirect />} />
              </Routes>
            )
          }
        />
      </Routes>
      {isAuthenticated && (
        <ShizuhaChatWidget
          sourceService="drive"
          welcomeMessage="Hello! I can help you find files, manage folders, and work with your cloud storage."
          showToolCalls={true}
        />
      )}
    </>
  )
}
