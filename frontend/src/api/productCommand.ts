// Keep a failed logical command's identity across retries. No request content
// or credentials are persisted; the key is a SHA-256 digest in this page only.
const pending = new Map<string, string>()

export async function productFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const method = (init.method || 'GET').toUpperCase()
  if (!['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) return fetch(url, init)
  let body = ''
  if (init.body instanceof FormData) {
    const parts: string[] = []
    for (const [key, value] of init.body.entries()) {
      if (typeof value === 'string') parts.push(JSON.stringify([key, value]))
      else {
        const digest = await crypto.subtle.digest('SHA-256', await value.arrayBuffer())
        parts.push(JSON.stringify([key, value.name, value.type, Array.from(new Uint8Array(digest))]))
      }
    }
    body = parts.join('\n')
  } else if (typeof init.body === 'string') body = init.body
  else if (init.body != null) return fetch(url, init)
  const bytes = new TextEncoder().encode(JSON.stringify([method, url, body]))
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))).map(v => v.toString(16).padStart(2, '0')).join('')
  const identity = pending.get(digest) || crypto.randomUUID()
  pending.set(digest, identity)
  const headers = new Headers(init.headers)
  headers.set('X-MentorFlow-Command-ID', identity)
  const response = await fetch(url, { ...init, headers })
  if (response.ok) pending.delete(digest)
  return response
}
