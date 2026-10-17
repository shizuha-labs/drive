import { test, expect, TEST_USER } from './fixtures.js'

// PLAT-8335: on failure, dump the page's full state to stdout — the CI
// workflow now captures pod logs (Complete-OR-Failed poll), so this lands
// in the run log and makes the red self-diagnosing. Console errors are
// captured from page load via an init script (buffered on the window).
test.afterEach(async ({ page }, testInfo) => {
  if (testInfo.status !== 'failed' && testInfo.status !== 'timedOut') return
  const state = await page
    .evaluate(() => ({
      href: location.href,
      readyState: document.readyState,
      rootChars: (document.getElementById('root') || document.body)?.innerHTML?.length ?? -1,
      access: (localStorage.getItem('shizuha_access_token') || 'NULL').slice(0, 24),
      accessIsExpiredMarker: (localStorage.getItem('shizuha_access_token') || '').includes('expired-signature'),
      refreshPresent: Boolean(localStorage.getItem('shizuha_refresh_token')),
      allXhr: performance
        .getEntriesByType('resource')
        .filter((e) => e.initiatorType === 'xmlhttprequest' || e.initiatorType === 'fetch')
        .map((e) => `${e.initiatorType} ${e.name} -> ${e.responseStatus ?? '?'}`),
      consoleErrors: (window.__plat8335ConsoleErrors || []).slice(0, 10),
      windowErrors: (window.__plat8335WindowErrors || []).slice(0, 10),
    }))
    .catch((e) => ({ dumpError: String(e) }))
  console.log('[PLAT-8335 state dump]', JSON.stringify(state))
})

/**
 * Authentication tests for Shizuha Drive
 *
 * The login flow works as follows:
 * 1. User visits /drive/ (unauthenticated)
 * 2. ProtectedRoute redirects to /id/login?continue=/drive/
 * 3. User logs in at Shizuha ID (/id/login)
 * 4. After successful login, redirects back to /drive/
 */

test.describe('Authentication', () => {

  test.describe('Unified Login Flow', () => {

    test('should redirect from /drive/my-drive to /id/login when unauthenticated', async ({ page }) => {
      // Clear any existing auth state
      await page.goto('/id/login')
      await page.evaluate(() => {
        localStorage.clear()
      })

      // Try to access an app deep-link directly (the public root shows the
      // Landing page instead of a forced login bounce — deep links redirect).
      await page.goto('/drive/my-drive')

      // Should redirect to Shizuha ID login with continue parameter
      await page.waitForURL(/.*\/id\/login.*continue/, { timeout: 15000 })

      // Verify we're on the login page
      await expect(page.locator('h1')).toContainText('Shizuha ID')
    })

    test('should login with valid credentials and redirect to Drive', async ({ page }) => {
      // Start at Shizuha ID login with continue parameter
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

      // The redirect happens via window.location.href, wait for it
      await page.waitForURL(/.*drive.*/, { timeout: 30000 })

      // Wait for page to load
      await page.waitForLoadState('domcontentloaded', { timeout: 15000 })

      // Verify we're on the Drive dashboard - look for Drive-specific UI elements
      // (the sidebar "My Drive" nav link is visible on desktop; the mobile-only
      // h1 "Drive" is lg:hidden so it must not be the assertion target).
      await expect(page.getByRole('link', { name: 'My Drive' })).toBeVisible({ timeout: 15000 })
    })

    test('should show error with invalid credentials', async ({ page }) => {
      await page.goto('/id/login')

      // Fill in wrong credentials
      await page.fill('input[id="username"]', 'wronguser')
      await page.fill('input[id="password"]', 'wrongpassword')

      // Submit form
      await page.click('button[type="submit"]')

      // Wait for error response from API
      await page.waitForResponse(response =>
        response.url().includes('/auth/login/') && response.status() >= 400,
        { timeout: 10000 }
      )

      // Wait a moment for state update
      await page.waitForTimeout(1000)

      // Should show error message
      await expect(
        page.locator('div').filter({ hasText: /Login failed|No active account|Invalid|credentials/i }).first()
      ).toBeVisible({ timeout: 5000 })

      // Should stay on login page
      await expect(page).toHaveURL(/.*\/id\/login/)
    })

  })

  test.describe('Session Persistence', () => {

    test('should persist authentication across page reload', async ({ authenticatedPage }) => {
      const page = authenticatedPage

      // Verify we're authenticated
      await page.waitForLoadState('networkidle', { timeout: 15000 })

      // Store current URL
      const currentUrl = page.url()

      // Reload the page
      await page.reload()

      // Should still be authenticated (not redirected to login)
      await page.waitForLoadState('networkidle', { timeout: 15000 })
      await expect(page).not.toHaveURL(/.*\/id\/login/)
    })

    test('should maintain session when navigating between pages', async ({ authenticatedPage }) => {
      const page = authenticatedPage

      // Navigate to different pages within Drive
      await page.goto('/drive/my-drive')
      await page.waitForLoadState('domcontentloaded')
      await expect(page).not.toHaveURL(/.*login/)

      await page.goto('/drive/shared')
      await page.waitForLoadState('domcontentloaded')
      await expect(page).not.toHaveURL(/.*login/)

      await page.goto('/drive/starred')
      await page.waitForLoadState('domcontentloaded')
      await expect(page).not.toHaveURL(/.*login/)

      await page.goto('/drive/trash')
      await page.waitForLoadState('domcontentloaded')
      await expect(page).not.toHaveURL(/.*login/)

      await page.goto('/drive/settings')
      await page.waitForLoadState('domcontentloaded')
      await expect(page).not.toHaveURL(/.*login/)
    })

  })

  test.describe('API Authentication', () => {

    test('should make authenticated API calls without 401 errors', async ({ authenticatedPage }) => {
      const page = authenticatedPage

      // Listen for API responses
      const apiResponses = []
      page.on('response', response => {
        if (response.url().includes('/drive/api/') || response.url().includes('/id/api/')) {
          apiResponses.push({
            url: response.url(),
            status: response.status()
          })
        }
      })

      // Navigate to trigger some API calls
      await page.goto('/drive/')
      await page.waitForLoadState('networkidle', { timeout: 15000 })

      // Wait for API calls to complete
      await page.waitForTimeout(3000)

      // Check that API calls were made and returned successful status
      const successfulCalls = apiResponses.filter(r => r.status >= 200 && r.status < 400)
      const failedAuth = apiResponses.filter(r => r.status === 401)

      // Log for debugging
      console.log('API responses:', apiResponses)

      // There should be some successful API calls
      expect(successfulCalls.length).toBeGreaterThan(0)

      // There should be no 401 errors after authentication
      expect(failedAuth.length).toBe(0)
    })

    test('should authenticate via API fixture', async ({ authenticatedApi }) => {
      // Test that authenticatedApi fixture works
      const response = await authenticatedApi.get('dashboard/')

      expect(response.ok()).toBeTruthy()
      const data = await response.json()
      expect(data).toBeDefined()
    })

    test('should get storage quota via API', async ({ authenticatedApi }) => {
      // Test getting storage quota
      const response = await authenticatedApi.get('quota/')

      expect(response.ok()).toBeTruthy()
      const data = await response.json()
      expect(data).toBeDefined()
      // Quota response should have storage info
      expect(data.used_bytes !== undefined || data.quota_bytes !== undefined || data.used !== undefined).toBeTruthy()
    })

    test('should list folders via API', async ({ authenticatedApi }) => {
      // Test listing folders
      const response = await authenticatedApi.get('folders/')

      expect(response.ok()).toBeTruthy()
      const data = await response.json()
      expect(data).toBeDefined()
      // Response should be an array or have results property
      expect(Array.isArray(data) || data.results !== undefined).toBeTruthy()
    })

    test('should list files via API', async ({ authenticatedApi }) => {
      // Test listing files
      const response = await authenticatedApi.get('files/')

      expect(response.ok()).toBeTruthy()
      const data = await response.json()
      expect(data).toBeDefined()
      // Response should be an array or have results property
      expect(Array.isArray(data) || data.results !== undefined).toBeTruthy()
    })

  })

  test.describe('Mid-session token expiry refresh (DRV-85)', () => {
    test('silently refreshes an expired access token and stays on /drive/', async ({
      page,
      playwright,
    }) => {
      // Establish a REAL refresh credential via the ID API (throttle-immune —
      // the authenticatedPage fixture's UI login intermittently fails to
      // persist tokens under CI's serial login load: runs 700/706 dumps showed
      // a client-shaped bounce with empty localStorage). The expired access
      // token + valid refresh pair is then injected before any page script
      // runs, exercising exactly the documented access-expiry recovery
      // boundary: the first Drive request must pre-emptively refresh (or
      // 401 → refresh once + retry) and the session must stay on /drive/ —
      // never bounce to home or /id/login.
      const baseUrl = process.env.BASE_URL || 'http://shizuha-nginx'
      const idContext = await playwright.request.newContext({
        baseURL: process.env.ID_API_URL || `${baseUrl}/id/api/`,
        extraHTTPHeaders: { 'Content-Type': 'application/json' },
      })
      const login = await idContext.post('auth/login/', {
        data: { username: TEST_USER.username, password: TEST_USER.password },
      })
      if (!login.ok()) {
        await idContext.dispose()
        throw new Error(`DRV-85 setup login failed: ${login.status()}`)
      }
      const { tokens } = await login.json()
      await idContext.dispose()
      if (!tokens?.refresh) throw new Error('DRV-85 setup: no refresh token in login response')

      const now = Math.floor(Date.now() / 1000)
      const header = btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }))
      const payload = btoa(
        JSON.stringify({
          user_id: 1,
          email: 'testuser@example.com',
          exp: now - 60, // expired 60s ago
          iat: now - 7200,
        }),
      )
      const expired = `${header}.${payload}.expired-signature`
      await page.addInitScript(() => {
        window.__plat8335ConsoleErrors = window.__plat8335ConsoleErrors || []
        window.__plat8335WindowErrors = window.__plat8335WindowErrors || []
        const origError = console.error.bind(console)
        console.error = (...args) => {
          try {
            window.__plat8335ConsoleErrors.push(args.map((a) => String(a?.message ?? a)).join(' ').slice(0, 300))
          } catch {}
          origError(...args)
        }
        window.addEventListener('error', (e) => {
          try {
            window.__plat8335WindowErrors.push(String(e.message).slice(0, 300))
          } catch {}
        })
        window.addEventListener('unhandledrejection', (e) => {
          try {
            window.__plat8335WindowErrors.push('rejection: ' + String(e.reason?.message ?? e.reason).slice(0, 300))
          } catch {}
        })
      })
      await page.addInitScript(
        ([access, refresh, user]) => {
          localStorage.setItem('shizuha_access_token', access)
          localStorage.setItem('shizuha_refresh_token', refresh)
          // AuthProvider keys the authenticated shell off BOTH the token and
          // the user object — without shizuha_user it renders the guest shell
          // (mounts, renders, makes zero API calls, never refreshes).
          localStorage.setItem('shizuha_user', user)
        },
        [expired, tokens.refresh, JSON.stringify({ id: 1, username: 'testuser', email: 'testuser@example.com' })],
      )

      // Navigate to the intended Drive route with the expired token in place.
      await page.goto('/drive/')

      // DRV-85: the session must NOT redirect to home or to /id/login — it
      // stays on /drive/ after the silent refresh.
      await page.waitForURL(/.*\/drive/, { timeout: 15000 })
      expect(page.url()).toContain('/drive/')

      // The silent refresh is ASYNC: the first authenticated request (the
      // Dashboard's getDashboard) triggers the request interceptor, which
      // awaits the /id/api/auth/refresh/ round trip before persisting the new
      // token. waitForURL resolves at navigation — before React even mounts —
      // so reading localStorage here races the persistence (premerge-e2e run
      // 691: the evaluate beat the refresh and saw the stale placeholder).
      // Poll until the stored token is actually replaced by a valid 3-part
      // JWT; a refresh failure clears the key, which keeps the poll red.
      await expect
        .poll(
          async () => {
            const token = await page.evaluate(() =>
              localStorage.getItem('shizuha_access_token'),
            )
            return (
              Boolean(token) &&
              !token.includes('expired-signature') &&
              token.split('.').length === 3
            )
          },
          { timeout: 15000, intervals: [250, 500, 1000] },
        )
        .toBe(true)

      // The access token was refreshed (no longer the expired placeholder).
      const refreshed = await page.evaluate(() =>
        localStorage.getItem('shizuha_access_token'),
      )
      expect(refreshed).not.toContain('expired-signature')
      expect(refreshed.split('.')).toHaveLength(3)
    })
  })

})
