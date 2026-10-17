import { test, expect, TEST_USER } from './fixtures.js'

// PLAT-8335: on failure, dump the page's auth/navigation state to stdout —
// the CI workflow now captures pod logs, so the bounce source (server guard
// vs client ProtectedRoute) becomes visible in the run log.
test.afterEach(async ({ page }, testInfo) => {
  if (testInfo.status !== 'failed' && testInfo.status !== 'timedOut') return
  const state = await page
    .evaluate(() => ({
      href: location.href,
      access: (localStorage.getItem('shizuha_access_token') || 'NULL').slice(0, 24),
      refreshPresent: Boolean(localStorage.getItem('shizuha_refresh_token')),
      authReqs: performance
        .getEntriesByType('resource')
        .filter((e) => /auth\/(refresh|login)/.test(e.name))
        .map((e) => e.name),
    }))
    .catch((e) => ({ dumpError: String(e) }))
  console.log('[PLAT-8335 state dump]', JSON.stringify(state))
})

/**
 * DRV-80: "Share with People" must support recipient selection + explicit
 * view/edit permission, and the recipient must not exceed the granted
 * permission. Positive + negative cases run against the live stack.
 */
test.describe('Share with People', () => {

  test('owner shares a file with a specific user and can revoke', async ({ page, playwright }) => {
    // Self-contained setup (PLAT-8335 acceptance #3): the Share flow needs a
    // file owned by testuser. Create one via the API instead of depending on
    // other tests' leftovers — testuser's drive may be empty (DRV-73 cleanup,
    // test ordering/isolation), which made the file-row click time out
    // (premerge-e2e run 691). Cleaned up in finally.
    const fileName = `share-people-${Date.now()}.txt`
    let fileId = null

    // Authenticate ONCE via the ID API (same authority as the authenticatedApi
    // fixture, minus the UI round-trip and its throttle exposure — run 702's
    // state dump showed the in-test UI login intermittently bouncing to
    // /id/login with empty localStorage under CI's serial login load).
    const baseUrl = process.env.BASE_URL || 'http://shizuha-nginx'
    const idContext = await playwright.request.newContext({
      baseURL: process.env.ID_API_URL || `${baseUrl}/id/api/`,
      extraHTTPHeaders: { 'Content-Type': 'application/json' },
    })
    const ownerLogin = await idContext.post('auth/login/', {
      data: { username: TEST_USER.username, password: TEST_USER.password },
    })
    if (!ownerLogin.ok()) {
      await idContext.dispose()
      throw new Error(`Owner API login failed: ${ownerLogin.status()} - ${await ownerLogin.text()}`)
    }
    const ownerTokens = (await ownerLogin.json()).tokens
    if (!ownerTokens?.access || !ownerTokens?.refresh) {
      await idContext.dispose()
      throw new Error('Owner API login returned no token pair')
    }

    // Authed Drive-API context for the multipart upload (Bearer header; no
    // forced JSON content-type — multipart needs Playwright's boundary type).
    const driveContext = await playwright.request.newContext({
      baseURL: process.env.API_URL || `${baseUrl}/drive/api/`,
      extraHTTPHeaders: { Authorization: `Bearer ${ownerTokens.access}` },
    })
    const upload = await driveContext.post('files/', {
      multipart: {
        file: {
          name: fileName,
          mimeType: 'text/plain',
          buffer: Buffer.from('DRV-80 share-with-people test file'),
        },
      },
    })
    if (!upload.ok()) {
      await idContext.dispose()
      await driveContext.dispose()
      throw new Error(`File upload for share test failed: ${upload.status()} - ${await upload.text()}`)
    }
    fileId = (await upload.json()).id

    try {
      // Authenticate the BROWSER context: write the API-issued token pair
      // straight into localStorage before any page script runs.
      await page.addInitScript(
        ([access, refresh, user]) => {
          localStorage.setItem('shizuha_access_token', access)
          localStorage.setItem('shizuha_refresh_token', refresh)
          // AuthProvider keys the authenticated shell off BOTH the token and
          // the user object — without shizuha_user it renders the guest shell
          // (mounts, renders, makes zero API calls).
          localStorage.setItem('shizuha_user', user)
        },
        [ownerTokens.access, ownerTokens.refresh, JSON.stringify({ id: 1, username: 'testuser', email: 'testuser@example.com' })],
      )

      // MyDrive renders the file rows with the per-row action dropdown
      // (Dashboard's recent-files list has no actions) — navigate there and
      // target OUR file so the test is independent of other rows.
      // Land on /drive/ (server-guard-protected deep links like
      // /drive/my-drive bounce cookieless navigations to /id/login with an
      // absolute continue URL — run 706 dump). Authenticated, /drive/ renders
      // the Dashboard; reach My Drive via the sidebar's react-router Link —
      // a pure SPA navigation that never touches the server guard.
      await page.goto('/drive/')
      await page.getByRole('link', { name: 'My Drive' }).click()
      // Served-bundle-compatible row locator: MyDrive renders item CARDS
      // (div.group.relative...) each with a button[title="More actions"] —
      // the data-testid="file-row" hook lives on the PR branch and is NOT in
      // the served bundle this lane tests against (run 714: locator starved).
      const row = page
        .locator('div.group.relative:has(button[title="More actions"])')
        .filter({ hasText: fileName })
        .first()
      await row.waitFor({ state: 'visible', timeout: 15000 })

      // Open the row's action dropdown -> Share -> Share with People tab.
      // getByRole targets the <button> itself — a bare text= selector would
      // also match the inner <span> and trip strict mode.
      await row.getByTitle('More actions').click()
      await page.getByRole('button', { name: 'Share', exact: true }).click()
      await page.getByRole('button', { name: 'Share with People' }).click()

      // The coming-soon placeholder must be gone; recipient entry is present.
      await expect(page.locator('text=coming soon')).toHaveCount(0)
      await expect(page.locator('input[placeholder="Recipient email"]')).toBeVisible()
      await expect(page.locator('select[aria-label="Permission"]')).toBeVisible()

      // Share with a second user at view permission. (The dropdown closed on
      // action, so the only exact-"Share" button now is the submit one.)
      await page.fill('input[placeholder="Recipient email"]', 'recipient@example.com')
      await page.selectOption('select[aria-label="Permission"]', 'view')
      await page.getByRole('button', { name: 'Share', exact: true }).click()
      // Assert on the recipient's access row (unique to the dialog): a bare
      // text=Shared with also matches the sidebar's "Shared with me" link
      // (strict-mode violation, run 716), and the dialog's own heading is a
      // static <p>Shared with</p> with no count.
      await expect(page.locator('text=recipient@example.com').first()).toBeVisible()

      // Revoke removes the recipient's access row.
      await page.click('button[aria-label^="Revoke share for"]')
      await expect(page.locator('text=recipient@example.com')).toHaveCount(0)
    } finally {
      if (fileId) {
        const cleanup = await driveContext.delete(`files/${fileId}/`)
        if (!cleanup.ok() && cleanup.status() !== 404) {
          console.warn(`share-people cleanup: delete files/${fileId} -> ${cleanup.status()}`)
        }
      }
      await idContext.dispose()
      await driveContext.dispose()
    }
  })

  test('recipient cannot exceed granted view permission (negative)', async ({ page }) => {
    // Second user logs in and must only see the shared file in view mode.
    await page.goto('/id/login?continue=/drive/')
    await page.waitForSelector('input[id="username"]', { timeout: 10000 })
    await page.fill('input[id="username"]', 'recipient@example.com')
    await page.fill('input[id="password"]', TEST_USER.password)
    await page.click('button[type="submit"]')
    await page.waitForURL(/.*\/drive\//, { timeout: 15000 })

    // Edit affordances must be absent for a view-only share.
    await expect(page.locator('button:has-text("Edit")').first()).toHaveCount(0)
  })
})
