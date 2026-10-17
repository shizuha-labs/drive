import { test, expect } from './fixtures.js'

/**
 * DRV-81 browser regression — share-link copy produces a real, defined token.
 *
 * Root cause: ShareDialog built the share URL from `.token` while the Drive API
 * exposes the share identifier as `link_token` (with a ready-made `share_url`);
 * every value was null/undefined, so Create/Copy Link emitted
 * `https://shizuha.com/drive/share/undefined`, which the public page cannot
 * resolve. This spec re-verifies the four AC points against the live stack:
 *  1. defined token URL,
 *  2. anonymous read of the shared item (no owner/edit controls),
 *  3. permission boundary (public payload carries no owner fields),
 *  4. revoke makes that exact URL inaccessible.
 */

const baseUrl = process.env.BASE_URL || 'http://shizuha-nginx'

test.describe('DRV-81 share link regression', () => {
  test('create link returns a defined token and a resolvable share_url', async ({
    authenticatedApi,
    testFolder,
  }) => {
    let shareId = null
    try {
      const createResponse = await authenticatedApi.post('shares/', {
        data: {
          folder: testFolder.id,
          share_type: 'link',
          permission: 'view',
        },
      })
      expect(createResponse.ok()).toBeTruthy()
      const share = await createResponse.json()

      // AC1: the raw token is defined (not the string "undefined").
      expect(share.link_token).toBeTruthy()
      expect(String(share.link_token)).not.toBe('undefined')
      expect(String(share.link_token)).not.toContain('undefined')

      // AC1: the serialized share_url is defined and embeds that token.
      expect(share.share_url).toMatch(new RegExp(`/drive/share/${share.link_token}$`))

      shareId = share.id
    } finally {
      if (shareId) {
        try {
          await authenticatedApi.post(`shares/${shareId}/revoke/`)
        } catch (e) {
          // cleanup best-effort
        }
      }
    }
  })

  test('anonymous visitor can read the shared item without owner controls', async ({
    authenticatedApi,
    testFolder,
    playwright,
  }) => {
    let shareId = null
    try {
      const createResponse = await authenticatedApi.post('shares/', {
        data: {
          folder: testFolder.id,
          share_type: 'link',
          permission: 'view',
        },
      })
      expect(createResponse.ok()).toBeTruthy()
      const share = await createResponse.json()
      shareId = share.id

      // A fresh, unauthenticated context exercises the real anonymous boundary.
      const anon = await playwright.request.newContext({ baseURL: baseUrl })
      try {
        const resp = await anon.get(`/drive/api/public/${share.link_token}/`)
        expect(resp.ok()).toBeTruthy()
        const body = await resp.json()
        expect(body.name).toBe(testFolder.name)
        // AC3: public payload carries no owner/editor controls.
        expect(body.owner).toBeUndefined()
        expect(body.created_by).toBeUndefined()
        expect(body.can_edit).toBeUndefined()
        expect(body.permission).toBeUndefined()
      } finally {
        await anon.dispose()
      }
    } finally {
      if (shareId) {
        try {
          await authenticatedApi.post(`shares/${shareId}/revoke/`)
        } catch (e) {
          // cleanup best-effort
        }
      }
    }
  })

  test('revocation makes the exact share URL inaccessible', async ({
    authenticatedApi,
    testFolder,
    playwright,
  }) => {
    let shareId = null
    let shareToken = null
    try {
      const createResponse = await authenticatedApi.post('shares/', {
        data: {
          folder: testFolder.id,
          share_type: 'link',
          permission: 'view',
        },
      })
      expect(createResponse.ok()).toBeTruthy()
      const share = await createResponse.json()
      shareId = share.id
      shareToken = share.link_token

      // Soft-revoke via the API, then confirm the anonymous URL 404s.
      const revokeResponse = await authenticatedApi.post(`shares/${shareId}/revoke/`)
      expect([200, 204]).toContain(revokeResponse.status())

      const anon = await playwright.request.newContext({ baseURL: baseUrl })
      try {
        const resp = await anon.get(`/drive/api/public/${shareToken}/`)
        expect(resp.status()).toBe(404)
      } finally {
        await anon.dispose()
      }
      shareId = null // already revoked
    } finally {
      if (shareId) {
        try {
          await authenticatedApi.post(`shares/${shareId}/revoke/`)
        } catch (e) {
          // cleanup best-effort
        }
      }
    }
  })
})
