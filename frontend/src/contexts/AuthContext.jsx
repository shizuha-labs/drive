import { createContext, useContext, useState, useEffect } from 'react'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    // Check for existing auth token from Shizuha ID
    const token = localStorage.getItem('shizuha_access_token')
    const userData = localStorage.getItem('shizuha_user')

    if (token && userData) {
      try {
        setUser(JSON.parse(userData))
      } catch (e) {
        console.error('Failed to parse user data:', e)
        localStorage.removeItem('shizuha_access_token')
        localStorage.removeItem('shizuha_user')
      }
    }
    setIsLoading(false)
  }, [])

  const logout = () => {
    localStorage.removeItem('shizuha_access_token')
    localStorage.removeItem('shizuha_refresh_token')
    localStorage.removeItem('shizuha_user')
    setUser(null)
    // Redirect to Shizuha ID logout
    window.location.href = '/id/logout'
  }

  return (
    <AuthContext.Provider
      value={{
        user,
        isAuthenticated: !!user,
        isLoading,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return context
}
