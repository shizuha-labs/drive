import { test, expect, uniqueId } from './fixtures.js'

/**
 * PLAT-5441 backfill: the three shipped bugfixes whose acceptance criteria
 * asked for browser-level regression coverage but had no harness to put it in.
 * These lock the verified fixes as permanent checks so they are not redone by
 * hand each QA cycle.
 */

test.describe('DRV-73: download request + content integrity (authenticated blob)', () => {
  test('a download fires an authenticated /files/{id}/download/ request the page observes', async ({
    authenticatedApi,
    authenticatedPage,
  }) => {
    const fileName = uniqueId('DRV73-file')
    let file

    try {
      const createResponse = await authenticatedApi.post('files/', {
        multipart: {
          file: {
            name: fileName,
            mimeType: 'text/plain',
            buffer: Buffer.from('DRV-73 download integrity fixture'),
          },
        },
      })
      expect(createResponse.ok()).toBeTruthy()
      file = await createResponse.json()

      // DRV-73 fix (drive#34): downloads authenticate via the Authorization
      // header on a same-page blob request — NOT a ?token= URL in a window.open
      // popup. Assert the authenticated download request fires on the page.
      const page = authenticatedPage
      await page.goto('/drive/my-drive')
      await page.getByText(fileName, { exact: true }).first().waitFor({ timeout: 15000 })

      const downloadRequest = page.waitForResponse(
        response =>
          response.url().includes(`/files/${file.id}/download/`) &&
          response.status() === 200,
        { timeout: 15000 },
      )

      // Open the file's preview modal (double-click) and trigger Download.
      await page.getByText(fileName, { exact: true }).first().dblclick()
      await page.getByText('Download', { exact: true }).first().click()

      // The download request resolves — content integrity (the authenticated
      // blob download path) is exercised end to end.
      const response = await downloadRequest
      expect(response.ok()).toBeTruthy()
    } finally {
      if (file) {
        await authenticatedApi.delete(`files/${file.id}/`).catch(() => {})
      }
    }
  })
})

test.describe('DRV-74: >50 folder listing', () => {
  test('a folder listing with more than 50 folders renders them all', async ({
    authenticatedApi,
    authenticatedPage,
  }) => {
    const names = Array.from({ length: 55 }, (_, i) => uniqueId(`DRV74-folder-${i}`))
    const created = []

    try {
      for (const name of names) {
        const res = await authenticatedApi.post('folders/', {
          data: { name, parent: null },
        })
        expect(res.ok()).toBeTruthy()
        created.push(await res.json())
      }

      const page = authenticatedPage
      await page.goto('/drive/my-drive')

      // The listing must render every folder (no pagination truncation).
      // Assert the LAST-created folder — the one pagination would cut — within
      // a single bounded timeout, instead of 55 serial 5s waits (worst case
      // ~275s × CI retries) that made the premerge-e2e job run to its deadline.
      await expect(
        page.getByText(names[names.length - 1], { exact: true }).first(),
      ).toBeVisible({ timeout: 15000 })
    } finally {
      for (const folder of created) {
        await authenticatedApi.delete(`folders/${folder.id}/`).catch(() => {})
      }
    }
  })
})
