'use strict';

/**
 * Shared JSON/FormData API client used by the asset, candidate, and preview
 * features. Keep the existing `api` and `json` names while extracting this
 * boundary so the current classic-script consumers continue to behave alike.
 */
async function api(path, options = {}) {
  const response = await fetch('/api' + path, {
    ...options,
    headers: {
      ...(options.body && !(options.body instanceof FormData)
        ? {'Content-Type': 'application/json'}
        : {}),
      ...options.headers,
    },
  });

  if (!response.ok) {
    let message;
    try {
      const body = await response.json();
      message = typeof body.detail === 'string'
        ? body.detail
        : JSON.stringify(body.detail || body);
    } catch {
      message = await response.text().catch(() => response.statusText);
    }
    const error = new Error(message || `请求失败 (${response.status})`);
    error.status = response.status;
    throw error;
  }

  return response.status === 204 ? null : response.json();
}

function json(method, body) {
  return {method, body: JSON.stringify(body)};
}
