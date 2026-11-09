// Dates are shown in the phone's local time as recorded (the ISO string's
// own offset), not the viewer's time zone: a message left at 7:32 pm says
// 7:32 pm even when you read it while traveling.

export function parts(iso) {
	const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso || '');
	if (!m) return null;
	const [, y, mo, d, h, mi] = m.map(Number);
	return { y, mo, d, h, mi, date: new Date(Date.UTC(y, mo - 1, d, h, mi)) };
}

const fmt = (opts) => new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', ...opts });
const longDate = fmt({ weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
const shortDate = fmt({ month: 'short', day: 'numeric', year: 'numeric' });
const monthYear = fmt({ month: 'long', year: 'numeric' });
const monthDay = fmt({ month: 'long', day: 'numeric' });

export const fmtDate = (iso) => (parts(iso) ? longDate.format(parts(iso).date) : '');
export const fmtShortDate = (iso) => (parts(iso) ? shortDate.format(parts(iso).date) : '');
export const fmtMonth = (iso) => (parts(iso) ? monthYear.format(parts(iso).date) : '');

export function fmtMonthDay(mmdd) {
	const [m, d] = mmdd.split('-').map(Number);
	return monthDay.format(new Date(Date.UTC(2024, m - 1, d)));
}

export function fmtTime(iso) {
	const p = parts(iso);
	if (!p) return '';
	return `${p.h % 12 || 12}:${String(p.mi).padStart(2, '0')} ${p.h < 12 ? 'am' : 'pm'}`;
}

export function fmtDuration(seconds) {
	const s = Math.max(0, Math.round(seconds || 0));
	return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

export function fmtAge(seconds) {
	if (seconds == null) return 'never';
	const s = Math.floor(seconds);
	for (const [unit, size] of [['day', 86400], ['hour', 3600], ['minute', 60]]) {
		if (s >= size) {
			const n = Math.floor(s / size);
			return `${n} ${unit}${n === 1 ? '' : 's'} ago`;
		}
	}
	return 'just now';
}

export function fmtSpan(seconds) {
	const s = Math.floor(seconds || 0);
	const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
	if (d) return `${d} d ${h} h`;
	if (h) return `${h} h ${m} min`;
	return `${m} min`;
}

export function fmtBytes(n) {
	n = Number(n || 0);
	for (const unit of ['B', 'KB', 'MB', 'GB']) {
		if (n < 1024) return unit === 'B' ? `${n} B` : `${n.toFixed(1)} ${unit}`;
		n /= 1024;
	}
	return `${n.toFixed(1)} TB`;
}

export const plural = (n, word, many = word + 's') => `${n} ${n === 1 ? word : many}`;

export const KIND_LABELS = {
	guest: 'Message',
	prompted: 'Answered a prompt',
	reply: 'Reply to a memory'
};

export function escapeHtml(s) {
	return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
}

/** Search snippets arrive with \x02 … \x03 around matches. */
export function highlight(snippet) {
	return escapeHtml(snippet).replaceAll('\x02', '<mark>').replaceAll('\x03', '</mark>');
}

export function messageTitle(m) {
	return m.title || fmtDate(m.recorded_at);
}
