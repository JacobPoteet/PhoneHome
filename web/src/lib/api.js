// Talking to the PhoneHome server. Every state-changing call carries the
// X-Requested-With header the server requires (CSRF protection).
import { goto } from '$app/navigation';
import { session } from './session.svelte.js';

export class ApiError extends Error {
	constructor(status, detail) {
		super(detail);
		this.status = status;
	}
}

export async function api(path, { method = 'GET', body, form, signal } = {}) {
	const headers = { 'X-Requested-With': 'phonehome' };
	let payload;
	if (form) {
		payload = form;
	} else if (body !== undefined) {
		headers['Content-Type'] = 'application/json';
		payload = JSON.stringify(body);
	}
	let r;
	try {
		r = await fetch(path, { method, headers, body: payload, signal, credentials: 'same-origin' });
	} catch (e) {
		if (e.name === 'AbortError') throw e;
		throw new ApiError(0, "Can't reach the PhoneHome server. Is it running?");
	}
	if (r.status === 401 && !path.startsWith('/api/auth/')) {
		session.user = null;
		const here = location.pathname + location.search;
		goto('/login?next=' + encodeURIComponent(here), { replaceState: true });
		throw new ApiError(401, 'Please sign in.');
	}
	const isJson = (r.headers.get('content-type') || '').includes('json');
	const data = isJson ? await r.json() : null;
	if (!r.ok) {
		const detail = typeof data?.detail === 'string' ? data.detail : r.statusText || 'Something went wrong';
		throw new ApiError(r.status, detail);
	}
	return data;
}

export const qs = (params) => {
	const p = new URLSearchParams();
	for (const [k, v] of Object.entries(params)) {
		if (v !== undefined && v !== null && v !== '' && v !== false) p.set(k, String(v));
	}
	const s = p.toString();
	return s ? `?${s}` : '';
};
