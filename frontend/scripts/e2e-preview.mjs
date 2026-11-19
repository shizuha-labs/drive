// DRV-84 e2e preview: serve the PR-built drive SPA locally while proxying
// the shared surfaces to the live stack, so premerge-e2e validates the
// PR's own UI instead of the deployed production build (run 1140: the
// suite ran against shizuha-nginx, where the PR-new `file-restore`
// testid does not exist yet — the test could never pass pre-merge).
//
// Routing:
//   /drive/api/*  -> upstream (live backend via nginx)
//   /id/api/*     -> upstream
//   /id/*         -> upstream (live Shizuha ID SPA)
//   everything else -> ./dist (PR build, SPA fallback to /drive/index.html)
import http from 'node:http'
import { existsSync, readFileSync, statSync } from 'node:fs'
import { extname, join, normalize } from 'node:path'

const PORT = Number(process.env.E2E_PREVIEW_PORT || 4173)
const UPSTREAM = (process.env.E2E_UPSTREAM || 'http://shizuha-nginx.shizuha.svc.cluster.local').replace(/\/$/, '')
const DIST = process.env.E2E_DIST || './dist'
const MIME = {
  '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css',
  '.json': 'application/json', '.png': 'image/png', '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon', '.woff2': 'font/woff2', '.map': 'application/json',
}

function proxy(req, res) {
  const target = UPSTREAM + req.url
  const headers = { ...req.headers, host: new URL(UPSTREAM).host }
  const body = []
  req.on('data', (chunk) => body.push(chunk))
  req.on('end', () => {
    fetch(target, {
      method: req.method,
      headers,
      body: ['GET', 'HEAD'].includes(req.method) ? undefined : Buffer.concat(body),
    })
      .then(async (up) => {
        const buf = Buffer.from(await up.arrayBuffer())
        res.writeHead(up.status, Object.fromEntries(up.headers))
        res.end(buf)
      })
      .catch((err) => {
        res.writeHead(502, { 'content-type': 'text/plain' })
        res.end(`e2e-preview upstream error: ${err.message}`)
      })
  })
}

function serveStatic(res, urlPath) {
  // Drive SPA is built with base /drive/ — serve /drive/* from dist and
  // fall back to /drive/index.html for client-side routes.
  let rel = urlPath.startsWith('/drive/') ? urlPath.slice('/drive/'.length) : urlPath.slice(1)
  if (rel === '' || rel === '/') rel = 'index.html'
  let file = normalize(join(DIST, rel))
  if (!file.startsWith(normalize(DIST))) file = join(DIST, 'index.html')
  if (!existsSync(file) || statSync(file).isDirectory()) {
    file = join(DIST, 'index.html')
    if (!existsSync(file)) {
      res.writeHead(404, { 'content-type': 'text/plain' })
      res.end('e2e-preview: dist not built')
      return
    }
  }
  res.writeHead(200, { 'content-type': MIME[extname(file)] || 'application/octet-stream' })
  res.end(readFileSync(file))
}

const server = http.createServer((req, res) => {
  const path = (req.url || '/').split('?')[0]
  if (path.startsWith('/drive/api/') || path.startsWith('/id/api/') || path.startsWith('/id/') || path === '/id') {
    proxy(req, res)
    return
  }
  serveStatic(res, path)
})

server.listen(PORT, () => console.log(`e2e-preview listening on :${PORT} (upstream ${UPSTREAM})`))
