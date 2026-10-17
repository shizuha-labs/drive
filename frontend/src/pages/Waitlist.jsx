import { useState, useEffect } from 'react'
import { submitWaitlist } from '../services/api'
import { track } from '../services/track'

// VEN-150 — standalone /drive/waitlist page. A focused, minimal signup form
// separate from the full Landing page, for direct links from /drive/pricing,
// social, and ad campaigns.

const INTEREST_OPTIONS = [
  { value: '', label: 'What sounds interesting? (optional)' },
  { value: 'personal_backup', label: 'Personal file backup' },
  { value: 'photo_storage', label: 'Photo & media storage' },
  { value: 'team_collaboration', label: 'Team file sharing' },
  { value: 'developer', label: 'Developer / API access' },
  { value: 'other', label: 'Something else' },
]

export default function Waitlist() {
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [interest, setInterest] = useState('')
  const [status, setStatus] = useState('idle') // idle | submitting | success | error
  const [message, setMessage] = useState('')

  useEffect(() => {
    track('page_view', { page: 'drive_waitlist' })
  }, [])

  const onSubmit = async (e) => {
    e.preventDefault()
    if (status === 'submitting') return
    const trimmed = email.trim()
    if (!trimmed || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(trimmed)) {
      setStatus('error')
      setMessage('Please enter a valid email address.')
      return
    }
    setStatus('submitting')
    setMessage('')
    track('waitlist_submit', { source: 'drive_waitlist_page' })
    try {
      const res = await submitWaitlist({
        email: trimmed,
        name: name.trim() || undefined,
        interest: interest || undefined,
        source: 'drive_waitlist_page',
        referrer: typeof document !== 'undefined' ? document.referrer : '',
      })
      track('waitlist_email_captured', {
        already_on_waitlist: !!res?.already_on_waitlist,
      })
      setStatus('success')
      setMessage(
        res?.already_on_waitlist
          ? "You're already on the list — we'll be in touch."
          : "You're on the list! We'll email your invite when it's ready.",
      )
    } catch (err) {
      setStatus('error')
      setMessage(
        err?.response?.data?.email?.[0] ||
          err?.response?.data?.detail ||
          'Something went wrong. Please try again.',
      )
    }
  }

  if (status === 'success') {
    return (
      <div className="flex min-h-screen items-center justify-center bg-white px-6">
        <div className="w-full max-w-lg text-center">
          <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-full bg-green-100">
            <svg className="h-8 w-8 text-green-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
            </svg>
          </div>
          <h1 className="mt-6 text-3xl font-bold text-gray-900">You&apos;re on the list!</h1>
          <p className="mt-3 text-lg text-gray-600">{message}</p>
          <p className="mt-6 text-sm text-gray-500">
            <a href="/drive/welcome" className="text-blue-600 hover:underline">
              ← Back to Shizuha Drive
            </a>
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-b from-blue-50 to-white px-6 py-16">
      <div className="w-full max-w-lg">
        {/* Header */}
        <div className="text-center">
          <h1 className="text-3xl font-bold text-gray-900">Get early access to Shizuha Drive</h1>
          <p className="mt-3 text-gray-600">
            Free during early access · 5 GB storage · No payment required
          </p>
        </div>

        {/* Form */}
        <form onSubmit={onSubmit} className="mt-8 rounded-2xl border border-gray-200 bg-white p-8 shadow-sm" noValidate>
          <div className="space-y-5">
            {/* Email */}
            <div>
              <label htmlFor="waitlist-email" className="block text-sm font-medium text-gray-700">
                Email address <span className="text-red-500">*</span>
              </label>
              <input
                id="waitlist-email"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                className="mt-1 block w-full rounded-lg border border-gray-300 px-4 py-3 text-gray-900 placeholder-gray-400 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-200"
              />
            </div>

            {/* Name */}
            <div>
              <label htmlFor="waitlist-name" className="block text-sm font-medium text-gray-700">
                Your name
              </label>
              <input
                id="waitlist-name"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Jane Doe (optional)"
                className="mt-1 block w-full rounded-lg border border-gray-300 px-4 py-3 text-gray-900 placeholder-gray-400 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-200"
              />
            </div>

            {/* Interest */}
            <div>
              <label htmlFor="waitlist-interest" className="block text-sm font-medium text-gray-700">
                Interest
              </label>
              <select
                id="waitlist-interest"
                value={interest}
                onChange={(e) => setInterest(e.target.value)}
                className="mt-1 block w-full rounded-lg border border-gray-300 px-4 py-3 text-gray-900 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-200"
              >
                {INTEREST_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>

            {/* Error */}
            {status === 'error' && (
              <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3" role="alert">
                <p className="text-sm text-red-700">{message}</p>
              </div>
            )}

            {/* Submit */}
            <button
              type="submit"
              disabled={status === 'submitting'}
              className="w-full rounded-lg bg-blue-600 px-6 py-3 font-medium text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {status === 'submitting' ? 'Joining…' : 'Join the Waitlist'}
            </button>
          </div>

          <p className="mt-4 text-center text-xs text-gray-400">
            We&apos;ll only use your email to send your invite. No spam, no marketing.
          </p>
        </form>

        {/* Footer link */}
        <p className="mt-6 text-center text-sm text-gray-500">
          <a href="/drive/welcome" className="text-blue-600 hover:underline">
            Learn more about Shizuha Drive →
          </a>
        </p>
      </div>
    </div>
  )
}
