import { test as base } from '@playwright/test'

/**
 * Test fixtures for Shizuha Drive E2E tests
 * Provides authenticated user context, test data helpers, and API access
 *
 * Authentication flow:
 * - Login happens at /id/login (Shizuha ID)
 * - After login, user is redirected to /drive/ (Shizuha Drive)
 * - Tokens are stored in localStorage with shared keys
 */

// Test user credentials (same as other Shizuha services)
export const TEST_USER = {
  username: 'testuser',
  password: 'TestPass123',
  email: 'testuser@example.com'
}

// Extend base test with authentication and data fixtures
export const test = base.extend({
  // Authenticated page fixture - logs in via Shizuha ID
  authenticatedPage: async ({ page }, use) => {
    // Go to Shizuha ID login page with continue parameter
    await page.goto('/id/login?continue=/drive/')

    // Wait for form to be ready
    await page.waitForSelector('input[id="username"]', { timeout: 10000 })

    // Fill in credentials
    await page.fill('input[id="username"]', TEST_USER.username)
    await page.fill('input[id="password"]', TEST_USER.password)

    // Click submit
    await page.click('button[type="submit"]')

    // Wait for successful login API response
    await page.waitForResponse(
      response => response.url().includes('/auth/login/') && response.ok(),
      { timeout: 15000 }
    )

    // Wait for redirect to Drive
    await page.waitForURL(/.*drive.*/, { timeout: 30000 })

    // Wait for page to fully load and React to hydrate
    await page.waitForLoadState('networkidle', { timeout: 20000 })
    await page.waitForTimeout(1000)

    // Verify we're on the drive app - check we're not still on login
    const url = page.url()
    if (url.includes('/id/login')) {
      throw new Error('Still on login page after authentication')
    }

    // Use the authenticated page
    await use(page)
  },

  // API context for direct API testing (unauthenticated)
  apiContext: async ({ playwright }, use) => {
    const baseUrl = process.env.BASE_URL || 'http://shizuha-nginx'
    const apiContext = await playwright.request.newContext({
      baseURL: process.env.API_URL || `${baseUrl}/drive/api`,
      extraHTTPHeaders: {
        'Content-Type': 'application/json',
      },
    })

    await use(apiContext)

    await apiContext.dispose()
  },

  // Authenticated API context - uses Shizuha ID for login
  authenticatedApi: async ({ playwright }, use) => {
    const baseUrl = process.env.BASE_URL || 'http://shizuha-nginx'
    const driveApiUrl = process.env.API_URL || `${baseUrl}/drive/api/`
    const idApiUrl = process.env.ID_API_URL || `${baseUrl}/id/api/`

    const apiContext = await playwright.request.newContext({
      baseURL: idApiUrl,
      extraHTTPHeaders: {
        'Content-Type': 'application/json',
      },
    })

    // Login via Shizuha ID to get token
    const loginResponse = await apiContext.post('auth/login/', {
      data: {
        username: TEST_USER.username,
        password: TEST_USER.password
      }
    })

    // Check if login was successful
    if (!loginResponse.ok()) {
      const text = await loginResponse.text()
      throw new Error(`Login failed: ${loginResponse.status()} - ${text.substring(0, 200)}`)
    }

    const { tokens } = await loginResponse.json()

    // Create new context with auth header for Drive API. Playwright sets
    // Content-Type: application/json automatically when `data` is an object,
    // so we do NOT force it here — a forced JSON content-type would break
    // multipart uploads (DRV-73) which need Playwright's boundary content-type.
    const authContext = await playwright.request.newContext({
      baseURL: driveApiUrl,
      extraHTTPHeaders: {
        'Authorization': `Bearer ${tokens.access}`
      },
    })

    // Store the token for later use
    authContext.token = tokens.access

    await use(authContext)

    await apiContext.dispose()
    await authContext.dispose()
  },

  // Fixture to get or create a test folder
  testFolder: async ({ authenticatedApi }, use) => {
    // Try to create a unique test folder
    const folderName = `Test Folder ${Date.now()}`

    const createResponse = await authenticatedApi.post('folders/', {
      data: {
        name: folderName,
        parent: null
      }
    })

    let folder = null

    if (createResponse.ok()) {
      folder = await createResponse.json()
    } else {
      console.warn('Failed to create test folder:', await createResponse.text())
    }

    await use(folder)

    // Cleanup: delete the test folder after use
    if (folder) {
      try {
        await authenticatedApi.delete(`folders/${folder.id}/`)
      } catch (e) {
        console.warn('Failed to cleanup test folder:', e)
      }
    }
  },

  // Fixture to get storage quota
  storageQuota: async ({ authenticatedApi }, use) => {
    const response = await authenticatedApi.get('quota/')
    let quota = null

    if (response.ok()) {
      quota = await response.json()
    }

    await use(quota)
  },
})

export { expect } from '@playwright/test'

/**
 * Helper function to create unique test data
 */
export function uniqueId(prefix = 'test') {
  return `${prefix}_${Date.now()}_${Math.random().toString(36).substring(2, 7)}`
}

/**
 * Helper to format date as YYYY-MM-DD
 */
export function formatDate(date = new Date()) {
  return date.toISOString().split('T')[0]
}

/**
 * Helper to format file size
 */
export function formatFileSize(bytes) {
  if (bytes === 0) return '0 Bytes'
  const k = 1024
  const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
}

/**
 * Helper to wait for API response with retries
 */
export async function waitForApiResponse(page, urlPattern, options = {}) {
  const { timeout = 10000, expectedStatus = 200 } = options

  return page.waitForResponse(
    response => {
      const matchesUrl = typeof urlPattern === 'string'
        ? response.url().includes(urlPattern)
        : urlPattern.test(response.url())

      return matchesUrl && response.status() === expectedStatus
    },
    { timeout }
  )
}

/**
 * PLAT-5441: capture a request that fires in a window.open CHILD page.
 *
 * @zen's Playwright popup-listener blind spot (DRV-73): per-page request
 * listeners never observe requests made in a `window.open` child page, so a
 * popup-based flow can be reported as "no request occurred" when one did.
 * Attach a request listener to the child context/page instead.
 *
 * Usage:
 *   const popupRequest = expectPopupRequest(page, /download/)
 *   await page.click('[data-action="download"]')   // opens the popup
 *   await popupRequest                             // resolves when the child fires it
 */
export async function expectPopupRequest(page, urlPattern, options = {}) {
  const { timeout = 15000 } = options
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      page.off('popup', onPage)
      reject(new Error(`popup request matching ${urlPattern} never fired`))
    }, timeout)

    const onPage = async (popup) => {
      popup.on('request', (request) => {
        const matches = typeof urlPattern === 'string'
          ? request.url().includes(urlPattern)
          : urlPattern.test(request.url())
        if (matches) {
          clearTimeout(timer)
          page.off('popup', onPage)
          resolve(request)
        }
      })
    }
    page.on('popup', onPage)
  })
}
