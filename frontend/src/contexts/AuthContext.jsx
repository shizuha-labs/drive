import { createContext, useContext, useState, useEffect } from 'react'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    const initAuth = async () => {
      let token = localStorage.getItem('shizuha_access_token')
      let userData = localStorage.getItem('shizuha_user')

      if (!(token && userData)) {
        // ID-93: bootstrap from the HttpOnly Shizuha-ID session cookies.
        // localStorage alone leaves a fresh load (cleared site data, browser
        // restart, non-page login) bouncing to /id/login despite valid
        // cookies; this same-origin call lets the cookies seed the session.
        try {
          const response = await fetch('/id/api/auth/session-bootstrap/', {
            credentials: 'include',
          })
          if (response.ok) {
            const data = await response.json()
            localStorage.setItem('shizuha_access_token', data.tokens.access)
            localStorage.setItem('shizuha_refresh_token', data.tokens.refresh)
            localStorage.setItem('shizuha_user', JSON.stringify(data.user))
            token = data.tokens.access
            userData = JSON.stringify(data.user)
          }
        } catch {
          // No/invalid cookies — genuinely signed out.
        }
      }

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
    }

    initAuth()
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
