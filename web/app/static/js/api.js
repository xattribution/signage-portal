/** Time-bounded, same-origin API requests. Writes always carry the CSRF guard. */
export async function request(method, url, body, { etag, timeout = 15000 } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  const headers = { Accept: 'application/json' };
  if (method !== 'GET' && method !== 'HEAD') headers['X-Signage'] = '1';
  if (etag) headers['If-None-Match'] = etag;
  const options = { method, headers, credentials: 'same-origin', signal: controller.signal, cache: 'no-store' };
  if (body !== undefined) { headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(body); }
  try {
    const response = await fetch(url, options);
    if (response.status === 304) return { data: null, response };
    let data = null; try { data = await response.json(); } catch (_) {}
    if (!response.ok) {
      if (response.status === 401 && !url.startsWith('/auth/')) location.assign('/');
      const detail = data?.detail;
      const error = new Error(Array.isArray(detail) ? detail.map(e => e.msg).join('; ') : typeof detail === 'string' ? detail : `Request failed (${response.status})`);
      error.status = response.status; throw error;
    }
    return { data, response };
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('The server took too long to respond. Try again.');
    throw error;
  } finally { clearTimeout(timer); }
}
export async function api(method, url, body) { return (await request(method, url, body)).data; }
