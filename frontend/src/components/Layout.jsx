import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  Home,
  HardDrive,
  Users,
  Star,
  Trash2,
  Settings,
  Upload,
  FolderPlus,
  Menu,
  X,
  LogOut,
  BookOpen,
  User,
  ChevronDown,
} from 'lucide-react'
import { useState } from 'react'
import { useAuth } from '../contexts/AuthContext'
import { getQuota } from '../services/api'
import AppSwitcher from './AppSwitcher'
import { useEnabledServices } from '@shizuha/ui'
import SearchBar from './SearchBar'
import FolderTreeSidebar from './FolderTreeSidebar'

const navItems = [
  { path: '/', icon: Home, label: 'Dashboard' },
  { path: '/my-drive', icon: HardDrive, label: 'My Drive' },
  { path: '/shared', icon: Users, label: 'Shared with me' },
  { path: '/starred', icon: Star, label: 'Starred' },
  { path: '/trash', icon: Trash2, label: 'Trash' },
]

export default function Layout({ children }) {
  const location = useLocation()
  const navigate = useNavigate()
  const { user, logout } = useAuth()
  const enabledServices = useEnabledServices()
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const [userMenuOpen, setUserMenuOpen] = useState(false)

  const { data: quota } = useQuery({
    queryKey: ['quota'],
    queryFn: getQuota,
  })
  const totalUsagePercent = Number(quota?.total_usage_percent ?? quota?.usage_percent ?? 0)
  const totalUsedHuman = quota?.total_used_human || quota?.used_human

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Mobile header */}
      <header className="lg:hidden fixed top-0 left-0 right-0 z-50 bg-white border-b border-gray-200">
        <div className="flex items-center justify-between px-4 h-14">
          <div className="flex items-center gap-2">
            <AppSwitcher currentAppId="drive" enabledServices={enabledServices} />
            <HardDrive size={20} className="text-blue-600 flex-shrink-0" />
            <h1 className="text-lg font-semibold text-blue-600 whitespace-nowrap">Drive</h1>
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
              className="p-2 rounded-lg hover:bg-gray-100"
              aria-label="Toggle menu"
            >
              {mobileMenuOpen ? <X size={24} /> : <Menu size={24} />}
            </button>
          </div>
        </div>
      </header>

      {/* Mobile menu overlay */}
      {mobileMenuOpen && (
        <div
          className="lg:hidden fixed inset-0 z-40 bg-black/50"
          onClick={() => setMobileMenuOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside
        className={`fixed top-0 left-0 z-50 h-full w-64 bg-white border-r border-gray-200 transform transition-transform duration-200 ease-in-out lg:translate-x-0 ${
          mobileMenuOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <div className="flex flex-col h-full">
          {/* Sidebar header */}
          <div className="h-14 lg:h-16 flex items-center justify-between px-4 border-b border-gray-200">
            <div className="flex items-center gap-3 min-w-0">
              <AppSwitcher currentAppId="drive" enabledServices={enabledServices} />
              <div className="flex items-center gap-2 min-w-0">
                <HardDrive size={22} className="text-blue-600 flex-shrink-0" />
                <h1 className="text-xl font-bold text-blue-600 whitespace-nowrap">Drive</h1>
              </div>
            </div>
            <button
              onClick={() => setMobileMenuOpen(false)}
              className="lg:hidden p-1 rounded-lg hover:bg-gray-100"
              aria-label="Close menu"
            >
              <X size={20} />
            </button>
          </div>

          {/* Search (inside sidebar on desktop) */}
          <div className="hidden lg:block p-3">
            <SearchBar
              onFolderClick={(folder) => navigate(`/my-drive/${folder.id}`)}
            />
          </div>

          {/* Actions */}
          <div className="p-4 space-y-2">
            <Link
              to="/my-drive"
              onClick={() => setMobileMenuOpen(false)}
              className="flex items-center space-x-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 w-full"
            >
              <Upload size={18} />
              <span>Upload</span>
            </Link>
            <button className="flex items-center space-x-2 px-4 py-2 border border-gray-200 rounded-lg hover:bg-gray-50 w-full text-gray-700">
              <FolderPlus size={18} />
              <span>New Folder</span>
            </button>
          </div>

          {/* Navigation */}
          <nav className="flex-1 overflow-y-auto px-2 py-4">
            {navItems.map((item) => {
              const Icon = item.icon
              const isActive = location.pathname === item.path ||
                (item.path !== '/' && location.pathname.startsWith(item.path))
              return (
                <Link
                  key={item.path}
                  to={item.path}
                  onClick={() => setMobileMenuOpen(false)}
                  className={`flex items-center space-x-3 px-4 py-2 rounded-lg mb-1 ${
                    isActive
                      ? 'bg-blue-50 text-blue-700'
                      : 'text-gray-600 hover:bg-gray-100'
                  }`}
                >
                  <Icon size={20} />
                  <span>{item.label}</span>
                </Link>
              )
            })}

            {/* Folder tree sidebar */}
            <div className="border-t border-gray-100 mt-2 pt-2">
              <FolderTreeSidebar />
            </div>
          </nav>

          {/* Storage Usage */}
          {quota && (
            <div className="p-4 border-t border-gray-200">
              <div className="text-sm text-gray-600 mb-2">Storage across services</div>
              <div className="w-full bg-gray-200 rounded-full h-2">
                <div
                  className={`h-2 rounded-full ${
                    totalUsagePercent >= 90
                      ? 'bg-red-500'
                      : totalUsagePercent > 70
                      ? 'bg-yellow-500'
                      : 'bg-blue-600'
                  }`}
                  style={{ width: `${Math.min(totalUsagePercent, 100)}%` }}
                />
              </div>
              <div className="text-xs text-gray-500 mt-1">
                {totalUsedHuman} of {quota.quota_human}
              </div>
            </div>
          )}

          {/* Settings & Docs */}
          <div className="p-2 border-t border-gray-200 space-y-1">
            <Link
              to="/docs"
              onClick={() => setMobileMenuOpen(false)}
              className={`flex items-center space-x-3 px-4 py-2 rounded-lg ${
                location.pathname === '/docs'
                  ? 'bg-blue-50 text-blue-700'
                  : 'text-gray-600 hover:bg-gray-100'
              }`}
            >
              <BookOpen size={20} />
              <span>Documentation</span>
            </Link>
            <Link
              to="/settings"
              onClick={() => setMobileMenuOpen(false)}
              className={`flex items-center space-x-3 px-4 py-2 rounded-lg ${
                location.pathname === '/settings'
                  ? 'bg-blue-50 text-blue-700'
                  : 'text-gray-600 hover:bg-gray-100'
              }`}
            >
              <Settings size={20} />
              <span>Settings</span>
            </Link>
          </div>

          {/* User section */}
          <div className="p-3 border-t border-gray-200">
            <div className="relative">
              <button
                onClick={() => setUserMenuOpen(!userMenuOpen)}
                className="w-full flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-gray-100 transition-colors"
              >
                <div className="w-8 h-8 rounded-full bg-blue-100 flex items-center justify-center">
                  <User size={16} className="text-blue-600" />
                </div>
                <div className="flex-1 text-left min-w-0">
                  <p className="text-sm font-medium text-gray-900 truncate">
                    {user?.first_name || user?.username || 'User'}
                  </p>
                  <p className="text-xs text-gray-500 truncate">
                    {user?.email}
                  </p>
                </div>
                <ChevronDown size={16} className={`text-gray-400 transition-transform ${
                  userMenuOpen ? 'rotate-180' : ''
                }`} />
              </button>

              {/* User dropdown menu */}
              {userMenuOpen && (
                <>
                  <div
                    className="fixed inset-0 z-10"
                    onClick={() => setUserMenuOpen(false)}
                  />
                  <div className="absolute bottom-full left-0 right-0 mb-1 z-20 bg-white border border-gray-200 rounded-lg shadow-lg overflow-hidden">
                    <button
                      onClick={() => {
                        setUserMenuOpen(false)
                        logout()
                      }}
                      className="w-full flex items-center gap-3 px-4 py-3 text-sm text-gray-700 hover:bg-gray-100"
                    >
                      <LogOut size={16} />
                      <span>Sign out</span>
                    </button>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      </aside>

      {/* Main content */}
      <main className="lg:ml-64 pt-14 lg:pt-0 min-h-screen pb-16 lg:pb-0">
        <div className="p-4 lg:p-6">{children}</div>
      </main>

      {/* Mobile bottom navigation */}
      <nav className="lg:hidden fixed bottom-0 left-0 right-0 z-40 bg-white border-t border-gray-200 safe-area-inset-bottom">
        <div className="flex justify-around">
          {navItems.map((item) => {
            const Icon = item.icon
            const isActive = location.pathname === item.path ||
              (item.path !== '/' && location.pathname.startsWith(item.path))
            return (
              <Link
                key={item.path}
                to={item.path}
                className={`flex flex-col items-center py-2 px-3 min-w-0 transition-colors ${
                  isActive
                    ? 'text-blue-600'
                    : 'text-gray-500'
                }`}
              >
                <Icon size={20} />
                <span className="text-xs mt-1 truncate">{item.label}</span>
              </Link>
            )
          })}
        </div>
      </nav>
    </div>
  )
}
