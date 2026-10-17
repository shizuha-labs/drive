// DRV-22 (VEN-49) — basic client-side event tracking for the public /drive
// landing page. Pushes named events into window.dataLayer (the standard
// GA/GTM contract) so a tag manager can consume them when one is wired, and
// no-ops safely when none is present. `waitlist_submit` is ALSO persisted
// server-side by POST /drive/api/waitlist/ (source / user_agent / referrer),
// so the funnel is recoverable even without a front-end analytics tag.
export function track(event, props = {}) {
  try {
    if (typeof window === 'undefined') return
    window.dataLayer = window.dataLayer || []
    window.dataLayer.push({ event, ...props, ts: Date.now() })
    if (import.meta.env.DEV) {
      // eslint-disable-next-line no-console
      console.debug('[track]', event, props)
    }
  } catch {
    /* tracking must never break the page */
  }
}
