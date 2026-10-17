import axios from 'axios'

const API_BASE = import.meta.env.VITE_API_BASE || '/drive/api'
const ACCESS_TOKEN_KEY = 'shizuha_access_token'
const REFRESH_TOKEN_KEY = 'shizuha_refresh_token'
const REFRESH_URL = '/id/api/auth/refresh/'

const api = axios.create({
  baseURL: API_BASE,
  headers: {
    'Content-Type': 'application/json',
  },
})

function driveContinuePath() {
  const path = `${window.location.pathname || '/drive/'}${window.location.search || ''}${window.location.hash || ''}`
  return path.startsWith('/drive') ? path : '/drive/'
}

function loginUrl() {
  return `/id/login?continue=${encodeURIComponent(driveContinuePath())}`
}

function clearAuthState() {
  localStorage.removeItem(ACCESS_TOKEN_KEY)
  localStorage.removeItem(REFRESH_TOKEN_KEY)
  localStorage.removeItem('shizuha_user')
}

function redirectToLogin() {
  // Never recurse from the login page; always keep the intended /drive route.
  if (!window.location.pathname.startsWith('/id/login')) {
    window.location.href = loginUrl()
  }
}

function decodeJwtPayload(token) {
  try {
    const parts = token.split('.')
    if (parts.length !== 3) return null
    const base64 = parts[1].replace(/-/g, '+').replace(/_/g, '/')
    const padded = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), '=')
    return JSON.parse(atob(padded))
  } catch {
    return null
  }
}

function tokenExpiresSoon(token, skewSeconds = 30) {
  const payload = decodeJwtPayload(token)
  return Boolean(payload?.exp && payload.exp <= Math.floor(Date.now() / 1000) + skewSeconds)
}

// DRV-85: a 401 with an expired access token must refresh silently and retry —
// the previous interceptor hard-redirected to /id/login, and the ID page's own
// refresh landed on home, dropping the intended /drive/ route. Only redirect on
// an actual refresh failure. Mirrors the platform-standard hive interceptor.
function isTokenAuthError(error) {
  const status = error.response?.status
  if (status === 401) return true
  if (status !== 403) return false
  const detail = error.response?.data?.detail || error.response?.data?.error || ''
  return /invalid token|token is expired|token_not_valid|authentication credentials/i.test(String(detail))
}

let refreshPromise = null

async function refreshAccessToken() {
  const refreshToken = localStorage.getItem(REFRESH_TOKEN_KEY)
  if (!refreshToken) throw new Error('No refresh token available')

  if (!refreshPromise) {
    refreshPromise = axios
      .post(REFRESH_URL, { refresh: refreshToken })
      .then((response) => {
        const { access, refresh } = response.data || {}
        if (!access) throw new Error('Refresh response did not include an access token')
        localStorage.setItem(ACCESS_TOKEN_KEY, access)
        if (refresh) localStorage.setItem(REFRESH_TOKEN_KEY, refresh)
        return access
      })
      .finally(() => {
        refreshPromise = null
      })
  }
  return refreshPromise
}

// Pre-emptively refresh an expiring token so the first Drive request already
// carries a valid access token (no 401, no route loss).
api.interceptors.request.use(async (config) => {
  let token = localStorage.getItem(ACCESS_TOKEN_KEY)
  if (token && tokenExpiresSoon(token)) {
    try {
      token = await refreshAccessToken()
    } catch (refreshError) {
      clearAuthState()
      redirectToLogin()
      return Promise.reject(refreshError)
    }
  }
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// On an expired-token failure, refresh once and retry the original request;
// only bounce to Shizuha ID login when the refresh itself fails.
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config || {}
    if (isTokenAuthError(error) && !originalRequest._retry) {
      originalRequest._retry = true
      try {
        const access = await refreshAccessToken()
        originalRequest.headers = originalRequest.headers || {}
        originalRequest.headers.Authorization = `Bearer ${access}`
        return api(originalRequest)
      } catch (refreshError) {
        clearAuthState()
        redirectToLogin()
        return Promise.reject(refreshError)
      }
    }
    return Promise.reject(error)
  }
)

// DRV-22 (VEN-49) — public free-validation waitlist submit. Uses a bare axios
// call (NOT the shared `api` instance) so the unauthenticated landing page is
// not subject to the auth header / 401-login-redirect interceptor. The backend
// endpoint is AllowAny and idempotent per lower-cased email.
// Returns { ok, already_on_waitlist, id }.
export const submitWaitlist = (data) =>
  axios
    .post(`${API_BASE}/waitlist/`, data, { headers: { 'Content-Type': 'application/json' } })
    .then((r) => r.data)

// Dashboard
export const getDashboard = () => api.get('/dashboard/').then((r) => r.data)

// Quota
export const getQuota = () => api.get('/quota/').then((r) => r.data)
export const getStorageQuota = () => api.get('/quota/').then((r) => r.data)

// Folders
export const getFolders = (params) => api.get('/folders/', { params }).then((r) => r.data)
export const getRootFolders = () => api.get('/folders/root/').then((r) => {
  const data = r.data
  return Array.isArray(data)
    ? { count: data.length, next: null, previous: null, results: data }
    : data
})
export const getFolderTree = () => api.get('/folders/tree/').then((r) => r.data)
export const getFolderChildren = (parentId) => api.get('/folders/children/', { params: { parent: parentId } }).then((r) => r.data)
export const getRootFolderChildren = () => api.get('/folders/children/').then((r) => r.data)
export const getFolder = (id) => api.get(`/folders/${id}/`).then((r) => r.data)
export const createFolder = (data) => api.post('/folders/', data).then((r) => r.data)
export const updateFolder = (id, data) => api.patch(`/folders/${id}/`, data).then((r) => r.data)
export const deleteFolder = (id) => api.delete(`/folders/${id}/`)
export const restoreFolder = (id) => api.post(`/folders/${id}/restore/`).then((r) => r.data)
export const moveFolder = (id, parentId) => api.post(`/folders/${id}/move/`, { parent: parentId }).then((r) => r.data)

// Files
export const getFiles = (params) => api.get('/files/', { params }).then((r) => r.data)
export const getRootFiles = () => api.get('/files/root/').then((r) => r.data)
export const getFile = (id) => api.get(`/files/${id}/`).then((r) => r.data)
const RESUMABLE_THRESHOLD_BYTES = 50 * 1024 * 1024
const RESUMABLE_CHUNK_BYTES = 5 * 1024 * 1024

async function sha256Hex(file) {
  if (!window.crypto?.subtle) return ''
  const buf = await file.arrayBuffer()
  const hash = await window.crypto.subtle.digest('SHA-256', buf)
  return Array.from(new Uint8Array(hash)).map((b) => b.toString(16).padStart(2, '0')).join('')
}

export const uploadFile = async (file, folderId, onProgress) => {
  if (file.size >= RESUMABLE_THRESHOLD_BYTES) {
    const checksum = await sha256Hex(file)
    const session = await api.post('/uploads/resumable/', {
      name: file.name,
      size: file.size,
      mime_type: file.type || 'application/octet-stream',
      folder: folderId || null,
      checksum_sha256: checksum,
      chunk_size: RESUMABLE_CHUNK_BYTES,
    }).then((r) => r.data)

    let offset = session.uploaded_bytes || 0
    while (offset < file.size) {
      const chunk = file.slice(offset, Math.min(offset + RESUMABLE_CHUNK_BYTES, file.size))
      const formData = new FormData()
      formData.append('offset', offset)
      formData.append('chunk', chunk, file.name)
      const res = await api.put(`/uploads/resumable/${session.id}/chunk/`, formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      }).then((r) => r.data)
      offset = res.uploaded_bytes
      if (onProgress) onProgress(Math.round((offset / file.size) * 100))
    }
    return api.post(`/uploads/resumable/${session.id}/complete/`).then((r) => r.data)
  }

  const formData = new FormData()
  formData.append('file', file)
  if (folderId) {
    formData.append('folder', folderId)
  }

  return api.post('/files/', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    onUploadProgress: (event) => {
      if (onProgress && event.total) {
        const percent = Math.round((event.loaded / event.total) * 100)
        onProgress(percent)
      }
    },
  }).then((r) => r.data)
}
export const updateFile = (id, data) => api.patch(`/files/${id}/`, data).then((r) => r.data)
export const deleteFile = (id) => api.delete(`/files/${id}/`)
// DRV-73: the download endpoint authenticates via the `Authorization` header
// only — it ignores `?token=`, so the URL this used to build returned 401 and
// nothing was ever downloaded. Fetch through `api` (whose request interceptor
// attaches the bearer) and hand the browser a blob instead. Keeping the token
// out of the URL also keeps it out of server logs, history and Referer.
export const downloadFileBlob = async (id, filename) => {
  const response = await api.get(`/files/${id}/download/`, { responseType: 'blob' })
  const objectUrl = URL.createObjectURL(response.data)
  try {
    const link = document.createElement('a')
    link.href = objectUrl
    link.download = filename || 'download'
    document.body.appendChild(link)
    link.click()
    link.remove()
  } finally {
    // Revoked on the next tick: revoking synchronously can cancel the save in
    // some browsers before the click is dispatched.
    setTimeout(() => URL.revokeObjectURL(objectUrl), 0)
  }
}

// Same root cause as above: preview surfaces (<img>/<video>/<audio>/<object>
// src) cannot send an Authorization header either, so they need an
// already-authenticated blob URL. The CALLER owns revocation — revoking while
// the element still references the URL blanks the preview.
export const fetchFileObjectUrl = async (id) => {
  const response = await api.get(`/files/${id}/download/`, { responseType: 'blob' })
  return URL.createObjectURL(response.data)
}

export const fetchFileText = async (id) => {
  const response = await api.get(`/files/${id}/download/`, { responseType: 'text' })
  return response.data
}
export const restoreFile = (id) => api.post(`/files/${id}/restore/`).then((r) => r.data)
export const moveFile = (id, folderId) => api.post(`/files/${id}/move/`, { folder: folderId }).then((r) => r.data)
export const copyFile = (id, folderId) => api.post(`/files/${id}/copy/`, { folder: folderId }).then((r) => r.data)
export const toggleStar = (id) => api.post(`/files/${id}/star/`).then((r) => r.data)
export const getRecentFiles = () => api.get('/files/recent/').then((r) => r.data)
export const getStarredFiles = () => api.get('/files/starred/').then((r) => r.data)
export const getFileVersions = (id) => api.get(`/files/${id}/versions/`).then((r) => r.data)

// Shares
export const getShares = () => api.get('/shares/').then((r) => r.data)
export const createShare = (data) => api.post('/shares/', data).then((r) => r.data)
export const revokeShare = (id) => api.post(`/shares/${id}/revoke/`).then((r) => r.data)
// DRV-12 M-2: send the link password in the POST body (never a GET query param,
// which leaks to access logs / browser history / Referer). The backend returns
// {password_required:true} (401) until a correct password is POSTed.
export const getPublicShare = (token, password) =>
  api.post(`/public/${token}/`, { password, action: 'unlock' }).then((r) => r.data)

// Trash
export const getTrash = () => api.get('/trash/').then((r) => r.data)
export const getTrashedFiles = () => api.get('/trash/').then((r) => r.data)
export const restoreFromTrash = (id) => api.post(`/trash/${id}/restore/`).then((r) => r.data)
// DRV-75: folders restore via the FolderViewSet detail action, not the trash route.
export const restoreFolderFromTrash = (id) => api.post(`/folders/${id}/restore/`).then((r) => r.data)
export const deleteFolderForever = (id) => api.delete(`/folders/${id}/permanent/`)
export const deleteForever = (id) => api.delete(`/trash/${id}/permanent/`)
export const emptyTrash = () => api.post('/trash/empty/').then((r) => r.data)

// Activity
export const getActivity = () => api.get('/activity/').then((r) => r.data)

export default api
