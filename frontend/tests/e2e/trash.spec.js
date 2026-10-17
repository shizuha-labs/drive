import { test, expect, uniqueId } from './fixtures.js'

test.describe('Trash recovery', () => {
  test('shows a deleted parent and child folder and restores the subtree', async ({
    authenticatedApi,
    authenticatedPage,
  }) => {
    const parentName = uniqueId('DRV75-parent')
    const childName = uniqueId('DRV75-child')
    let parent

    try {
      const parentResponse = await authenticatedApi.post('folders/', {
        data: { name: parentName, parent: null },
      })
      expect(parentResponse.ok()).toBeTruthy()
      parent = await parentResponse.json()

      const childResponse = await authenticatedApi.post('folders/', {
        data: { name: childName, parent: parent.id },
      })
      expect(childResponse.ok()).toBeTruthy()
      const child = await childResponse.json()

      const deleteResponse = await authenticatedApi.delete(`folders/${parent.id}/`)
      expect(deleteResponse.status()).toBe(204)

      const trashResponse = await authenticatedApi.get('trash/')
      expect(trashResponse.ok()).toBeTruthy()
      const trash = await trashResponse.json()
      expect(trash.folders.map((folder) => folder.id)).toEqual(
        expect.arrayContaining([parent.id, child.id]),
      )

      const page = authenticatedPage
      const trashRequest = page.waitForResponse(
        (response) => response.url().includes('/drive/api/trash/') && response.ok(),
      )
      await page.goto('/drive/trash')
      await trashRequest

      // DRV-75 P1b (revi): scope every Trash visibility assertion to the Trash
      // LIST container. After restore the folder reappears in the Drive sidebar,
      // so a page-global getByText(name) match stays visible and a page-global
      // .toBeHidden() would false-fail on the sidebar occurrence, not the trash row.
      const trashList = page.getByTestId('trash-list')
      await expect(trashList.getByText(parentName, { exact: true })).toBeVisible()
      await expect(trashList.getByText(childName, { exact: true })).toBeVisible()

      const rowFor = (name) =>
        trashList
          .getByText(name, { exact: true })
          .locator('xpath=ancestor::div[contains(@class,"justify-between")][1]')
      const parentRow = rowFor(parentName)
      const childRow = rowFor(childName)

      // DRV-75 P1a (revi): the nested child whose parent is also trashed must NOT
      // expose its own Restore — restoring it alone would orphan it under a still
      // trashed parent. Only the subtree root (parent) offers Restore; the child
      // shows the "restores with parent" hint. This is the child-row regression.
      await expect(parentRow.getByTestId('folder-restore')).toBeVisible()
      await expect(childRow.getByTestId('folder-restore')).toHaveCount(0)
      await expect(childRow.getByTestId('folder-restore-with-parent')).toBeVisible()

      const restoreResponse = page.waitForResponse(
        (response) =>
          response.url().includes(`/drive/api/folders/${parent.id}/restore/`) &&
          response.request().method() === 'POST',
      )
      await parentRow.getByTestId('folder-restore').click()
      expect((await restoreResponse).ok()).toBeTruthy()

      // Scoped to the trash list: both rows leave the trash view (the parent is
      // now browser-reachable in Drive, and the child came back WITH it).
      await expect(trashList.getByText(parentName, { exact: true })).toBeHidden()
      await expect(trashList.getByText(childName, { exact: true })).toBeHidden()

      await expect
        .poll(async () => {
          const [restoredParent, restoredChild] = await Promise.all([
            authenticatedApi.get(`folders/${parent.id}/`),
            authenticatedApi.get(`folders/${child.id}/`),
          ])
          return [
            (await restoredParent.json()).is_trashed,
            (await restoredChild.json()).is_trashed,
          ]
        })
        .toEqual([false, false])
    } finally {
      if (parent) {
        await authenticatedApi.delete(`folders/${parent.id}/`)
      }
    }
  })
})
