// App-wide state: who's signed in, counts for the nav badges, toasts.
import { api } from './api.js';

export const session = $state({
	user: null,
	firstRun: false,
	version: '',
	checked: false,
	stats: null
});

export async function checkSession() {
	const me = await api('/api/auth/me');
	session.user = me.user;
	session.firstRun = me.first_run;
	session.version = me.version;
	session.checked = true;
	return me;
}

export async function refreshStats() {
	if (!session.user) return;
	try {
		session.stats = await api('/api/stats');
	} catch {
		/* the nav badge can wait */
	}
}

export const toasts = $state([]);
let nextToast = 1;

export function toast(message, { kind = 'ok', timeout = 4500, action } = {}) {
	const id = nextToast++;
	toasts.push({ id, message, kind, action });
	if (timeout) setTimeout(() => dismiss(id), timeout);
	return id;
}

export function dismiss(id) {
	const i = toasts.findIndex((t) => t.id === id);
	if (i >= 0) toasts.splice(i, 1);
}

export function toastError(e) {
	if (e?.status === 401) return;
	toast(e?.message || String(e), { kind: 'err', timeout: 7000 });
}
