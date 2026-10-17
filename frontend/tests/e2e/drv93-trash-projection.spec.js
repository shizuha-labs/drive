import { test, expect, uniqueId } from './fixtures.js'

/**
 * DRV-93 — [Drive][Folder CRUD] Trashed folder remains visible in My Drive
 * while also appearing in Trash.
 *
 * Regression: after create → rename → trash, a fresh navigation to My Drive
 * must NOT show the folder anywhere (grid OR sidebar), and Trash must show it.
 *
 * The frontend bug was that trashing/moving/renaming a folder only invalidated
 * the grid + tree queries, leaving the sidebar's ['root-folder-children'] and
 * ['folder-children', id] caches (staleTime:30s) holding the trashed folder.
 */
test.describe('DRV-93 folder trash projection', () => {
  test('trashed folder leaves My Drive and appears only in Trash after reload', async ({
    authenticatedApi,
    authenticatedPage,
  }) => {
    const folderName = uniqueId('DRV93-folder')
    let folder

    try {
      // 1. Create the folder at root (My Drive).
      const createResponse = await authenticatedApi.post('folders/', {
        data: { name: folderName, parent: null },
      })
      expect(createResponse.ok()).toBeTruthy()
      folder = await createResponse.json()

      // 2. Rename it (exact repro step).
      const renamed = `${folderName}-renamed`
      const renameResponse = await authenticatedApi.patch(`folders/${folder.id}/`, {
        data: { name: renamed },
      })
      expect(renameResponse.ok()).toBeTruthy()

      // 3. Move to trash (soft delete).
      const deleteResponse = await authenticatedApi.delete(`folders/${folder.id}/`)
      expect(deleteResponse.status()).toBe(204)

      // 4. Fresh navigation to My Drive — the renamed folder must be absent
      //    from BOTH the grid and the sidebar.
      const page = authenticatedPage
      await page.goto('/drive/my-drive')
      await page.waitForLoadState('networkidle', { timeout: 20000 })

      // Scope to the main content area; the folder must not render anywhere
      // on the My Drive page after a fresh reload.
      await expect(page.getByText(renamed, { exact: true })).toHaveCount(0)

      // 5. Fresh navigation to Trash — the renamed folder must be present.
      await page.goto('/drive/trash')
      await page.waitForLoadState('networkidle', { timeout: 20000 })

      const trashList = page.getByTestId('trash-list')
      await expect(trashList.getByText(renamed, { exact: true })).toBeVisible()

      // Backend truth: the folder is trashed and absent from the active root.
      const [rootFolders, trash] = await Promise.all([
        authenticatedApi.get('folders/root/'),
        authenticatedApi.get('trash/'),
      ])
      const rootIds = (await rootFolders.json()).map((f) => f.id)
      const trashIds = (await trash.json()).folders.map((f) => f.id)
      expect(rootIds).not.toContain(folder.id)
      expect(trashIds).toContain(folder.id)
    } finally {
      if (folder) {
        // Cleanup: re-delete (idempotent) so the trashed folder does not linger
        // in the active set; trash retention purges it after the restore window.
        await authenticatedApi.delete(`folders/${folder.id}/`).catch(() => {})
      }
    }
  })
})
