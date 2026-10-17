import { useEffect, useRef, useState } from 'react'
import { submitWaitlist } from '../services/api'
import { track } from '../services/track'

// DRV-22 (VEN-49) — public /drive landing + free-validation waitlist.
// Copy approved by Yuki (wiki: "Copy: shizuha.com/drive Landing Page — Free
// Early Access"). DRV-21 (external least-privilege signup) is still open, so
// this uses the SOFTER trust-copy variant and keeps "create your account" as
// placeholder copy (no live signup link yet). No payment / paid-tier UI —
// VEN-49 is free-validation only.

const FEATURES = [
  {
    title: 'Upload & Organize',
    body: 'Drag and drop files, create folders, and keep everything in one place. Supports all file types up to 500 MB per file.',
  },
  {
    title: 'Share Securely',
    body: 'Share files or folders with a secure link. You control who sees what — nothing is public unless you share it explicitly.',
  },
  {
    title: 'Access Anywhere',
    body: 'Your files are available from any device via your Shizuha account. Simple, fast, and always with you.',
  },
]

const STEPS = [
  {
    title: 'Join the waitlist',
    body: "Enter your email below. We'll send an invite when your spot is ready.",
  },
  {
    title: 'Create your account',
    body: "When your invite arrives, you'll sign up via Shizuha ID. It's free; no payment details needed.",
  },
  {
    title: 'Start uploading',
    body: 'You get 5 GB of personal storage, ready to use immediately.',
  },
]

const LIMITS = [
  ['Free storage', '5 GB'],
  ['Max file size', '500 MB'],
  ['Account type', 'Drive-only (scoped to your personal files)'],
  ['Cost', 'Free during early access'],
]

function WaitlistForm({ id }) {
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [status, setStatus] = useState('idle') // idle | submitting | success | error
  const [message, setMessage] = useState('')

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
    track('waitlist_submit', { source: 'drive_landing' })
    try {
      const res = await submitWaitlist({
        email: trimmed,
        name: name.trim() || undefined,
        source: 'drive_landing',
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
      <div
        className="rounded-xl border border-green-200 bg-green-50 px-6 py-5 text-green-800"
        role="status"
      >
        <p className="font-medium">{message}</p>
      </div>
    )
  }

  return (
    <form onSubmit={onSubmit} className="space-y-3" noValidate aria-describedby={`${id}-help`}>
      <div className="flex flex-col gap-3 sm:flex-row">
        <input
          type="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@example.com"
          aria-label="Email address"
          className="w-full flex-1 rounded-lg border border-gray-300 px-4 py-3 text-gray-900 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-200"
        />
        <button
          type="submit"
          disabled={status === 'submitting'}
          className="rounded-lg bg-blue-600 px-6 py-3 font-medium text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {status === 'submitting' ? 'Joining…' : 'Join the Waitlist'}
        </button>
      </div>
      <input
        type="text"
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="Your name (optional)"
        aria-label="Your name (optional)"
        className="w-full rounded-lg border border-gray-300 px-4 py-3 text-gray-900 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-200"
      />
      {status === 'error' && (
        <p className="text-sm text-red-600" role="alert">
          {message}
        </p>
      )}
      <p id={`${id}-help`} className="text-sm text-gray-500">
        We&apos;ll only use your email to send your invite. No spam.
      </p>
    </form>
  )
}

export default function Landing() {
  const waitlistRef = useRef(null)

  useEffect(() => {
    track('page_view', { page: 'drive_landing' })
  }, [])

  const scrollToWaitlist = () => {
    track('cta_click', { target: 'waitlist' })
    waitlistRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  return (
    <div className="min-h-screen bg-white text-gray-900">
      {/* Hero */}
      <header className="bg-gradient-to-b from-blue-50 to-white">
        <div className="mx-auto max-w-5xl px-6 py-20 text-center">
          <h1 className="text-4xl font-bold tracking-tight sm:text-5xl">
            Your files, simply stored.
          </h1>
          <p className="mx-auto mt-5 max-w-2xl text-lg text-gray-600">
            Shizuha Drive is a personal cloud storage service built for people, not enterprises.
            Store, organize, and share your files securely — free during early access.
          </p>
          <div className="mt-8">
            <button
              type="button"
              onClick={scrollToWaitlist}
              className="rounded-lg bg-blue-600 px-8 py-4 text-lg font-medium text-white transition hover:bg-blue-700"
            >
              Join the Early Access Waitlist →
            </button>
            <p className="mt-3 text-sm text-gray-500">
              Free · 5 GB included · No payment required · Invite-only during early access
            </p>
          </div>
        </div>
      </header>

      {/* Feature strip */}
      <section className="mx-auto max-w-5xl px-6 py-16">
        <div className="grid gap-8 md:grid-cols-3">
          {FEATURES.map((f) => (
            <div key={f.title} className="rounded-xl border border-gray-100 bg-gray-50 p-6">
              <h3 className="text-lg font-semibold">{f.title}</h3>
              <p className="mt-2 text-gray-600">{f.body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* How it works */}
      <section className="bg-gray-50">
        <div className="mx-auto max-w-5xl px-6 py-16">
          <h2 className="text-center text-2xl font-bold">Get started in three steps</h2>
          <ol className="mt-10 grid gap-8 md:grid-cols-3">
            {STEPS.map((s, i) => (
              <li key={s.title} className="text-center">
                <div className="mx-auto flex h-10 w-10 items-center justify-center rounded-full bg-blue-600 font-semibold text-white">
                  {i + 1}
                </div>
                <h3 className="mt-4 font-semibold">{s.title}</h3>
                <p className="mt-2 text-gray-600">{s.body}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      {/* Trust & privacy — softer variant while DRV-21 (least-privilege) is open */}
      <section className="mx-auto max-w-3xl px-6 py-16">
        <h2 className="text-2xl font-bold">Your files are yours.</h2>
        <p className="mt-4 text-gray-600">
          Drive is a personal storage service — your account is created specifically for your files.
          We&apos;re building with privacy as a baseline, not an afterthought.
        </p>
        <ul className="mt-6 space-y-2 text-gray-600">
          <li>• Files you upload are visible only to you until you choose to share them.</li>
          <li>• Shared links can be revoked at any time.</li>
          <li>• We do not scan, train on, or monetize the content of your files.</li>
        </ul>
      </section>

      {/* Storage & limits */}
      <section className="bg-gray-50">
        <div className="mx-auto max-w-3xl px-6 py-16">
          <h2 className="text-2xl font-bold">Simple, honest limits</h2>
          <div className="mt-6 overflow-hidden rounded-xl border border-gray-200">
            <table className="w-full text-left">
              <tbody>
                {LIMITS.map(([k, v], idx) => (
                  <tr key={k} className={idx % 2 ? 'bg-white' : 'bg-gray-50'}>
                    <th scope="row" className="px-5 py-3 font-medium text-gray-700">
                      {k}
                    </th>
                    <td className="px-5 py-3 text-gray-600">{v}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-3 text-sm text-gray-500">
            We&apos;ll let you know well in advance if limits change. No surprises.
          </p>
        </div>
      </section>

      {/* Waitlist CTA */}
      <section ref={waitlistRef} className="mx-auto max-w-2xl px-6 py-20">
        <div className="rounded-2xl border border-gray-200 bg-white p-8 shadow-sm">
          <h2 className="text-2xl font-bold">Get early access to Shizuha Drive</h2>
          <p className="mt-3 text-gray-600">
            Be among the first to use Shizuha Drive. We&apos;re rolling out access gradually to keep
            the experience smooth for everyone.
          </p>
          <div className="mt-6">
            <WaitlistForm id="waitlist" />
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-gray-100">
        <div className="mx-auto max-w-3xl px-6 py-10 text-center text-sm text-gray-500">
          Shizuha Drive is currently in early access validation. Storage limits and features may
          change. By joining the waitlist, you agree to receive a single invitation email.
        </div>
      </footer>
    </div>
  )
}
