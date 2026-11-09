// One audio element for the whole app: playing a message anywhere stops the
// previous one, and the first play of a message marks it as heard.
import { api } from './api.js';
import { refreshStats } from './session.svelte.js';

export const player = $state({ id: null, playing: false, time: 0, duration: 0 });
let audio = null;
const heardCallbacks = new Set();

function element() {
	if (audio) return audio;
	audio = new Audio();
	audio.preload = 'none';
	audio.addEventListener('timeupdate', () => (player.time = audio.currentTime));
	audio.addEventListener('durationchange', () => (player.duration = audio.duration || 0));
	audio.addEventListener('play', () => (player.playing = true));
	audio.addEventListener('pause', () => (player.playing = false));
	audio.addEventListener('ended', () => {
		player.playing = false;
		player.time = 0;
	});
	return audio;
}

export function onHeard(fn) {
	heardCallbacks.add(fn);
	return () => heardCallbacks.delete(fn);
}

export async function markHeard(msg) {
	if (msg.heard) return;
	msg.heard = true;
	try {
		await api(`/api/messages/${msg.id}/heard`, { method: 'POST', body: { heard: true } });
		heardCallbacks.forEach((fn) => fn(msg.id));
		refreshStats();
	} catch {
		/* not important enough to bother anyone */
	}
}

export function toggle(msg) {
	const a = element();
	if (player.id === msg.id) {
		if (a.paused) a.play();
		else a.pause();
		return;
	}
	a.pause();
	a.src = msg.audio_url;
	player.id = msg.id;
	player.time = 0;
	player.duration = msg.duration_s || 0;
	a.play().catch(() => {});
	markHeard(msg);
}

export function seek(msg, fraction) {
	const a = element();
	if (player.id !== msg.id) toggle(msg);
	const go = () => (a.currentTime = fraction * (a.duration || msg.duration_s || 0));
	if (a.readyState >= 1) go();
	else a.addEventListener('loadedmetadata', go, { once: true });
}

export function stopShared() {
	audio?.pause();
}
