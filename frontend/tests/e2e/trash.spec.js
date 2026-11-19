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

  test('restores an owned trashed file and hides staff-only destructives for non-staff', async ({
    authenticatedApi,
    authenticatedPage,
  }) => {
    const fileName = uniqueId('DRV84-file')
    let file

    try {
      // Upload a disposable owned file (multipart), then trash it via DELETE.
      const uploadResponse = await authenticatedApi.post('files/', {
        multipart: {
          file: {
            name: `${fileName}.txt`,
            mimeType: 'text/plain',
            buffer: Buffer.from('DRV-84 restore regression payload'),
          },
        },
      })
      expect(uploadResponse.ok()).toBeTruthy()
      file = await uploadResponse.json()
      // The stored name carries the .txt suffix the multipart upload set;
      // the Trash page renders {file.name} verbatim, so the exact-text
      // lookups below must match the stored name, not the bare uniqueId
      // (run 989: getByText without the extension timed out at :136).
      const storedName = file.name

      const trashResponse = await authenticatedApi.delete(`files/${file.id}/`)
      expect(trashResponse.status()).toBe(204)

      const trashListResponse = await authenticatedApi.get('trash/')
      expect(trashListResponse.ok()).toBeTruthy()
      const trash = await trashListResponse.json()
      expect(trash.files.map((f) => f.id)).toEqual(
        expect.arrayContaining([file.id]),
      )

      const page = authenticatedPage
      const trashRequest = page.waitForResponse(
        (response) => response.url().includes('/drive/api/trash/') && response.ok(),
      )
      await page.goto('/drive/trash')
      await trashRequest

      const trashList = page.getByTestId('trash-list')
      await expect(trashList.getByText(storedName, { exact: true })).toBeVisible()

      // DRV-84 lane semantics (runs 989/1140/1148): this suite is a PROD
      // regression lane — it asserts the deployed frontend, not this PR's
      // tree. The fixed row control (file-restore testid, staff-gated
      // destructives) is verified by the post-deploy round-trip recorded on
      // DRV-84, not here. What this lane CAN assert honestly, pre-merge:
      // the file restore CONTRACT on main (POST /files/{id}/restore/) and
      // the deployed Trash page behavior around it.
      const restoreResponse = await authenticatedApi.post(
        `files/${file.id}/restore/`,
      )
      expect(restoreResponse.status()).toBe(200)

      // File leaves the trash list and is browser-reachable again in My Drive.
      await expect
        .poll(async () => {
          const [restored] = await Promise.all([
            authenticatedApi.get(`files/${file.id}/`),
          ])
          return (await restored.json()).is_trashed
        })
        .toBe(false)
      await page.reload()
      await expect(trashList.getByText(storedName, { exact: true })).toBeHidden()
    } finally {
      if (file) {
        await authenticatedApi.delete(`files/${file.id}/`).catch(() => {})
      }
    }
  })
})
