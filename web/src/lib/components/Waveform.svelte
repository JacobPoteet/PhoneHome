<script>
	import { untrack } from 'svelte';
	import WaveSurfer from 'wavesurfer.js';
	import { fmtDuration } from '#lib/format.js';
	import Icon from './Icon.svelte';

	let { src, duration = 0, time = $bindable(0), playing = $bindable(false), onplay } = $props();

	let container;
	let ws = null;
	let ready = $state(false);
	let failed = $state(false);
	let total = $state(untrack(() => duration)); // until the audio's real length is known

	function css(name) {
		return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
	}

	$effect(() => {
		const url = src; // re-create when the audio changes
		ready = false;
		failed = false;
		ws = WaveSurfer.create({
			container,
			url,
			height: 72,
			waveColor: css('--line'),
			progressColor: css('--accent'),
			cursorColor: css('--accent'),
			cursorWidth: 2,
			barWidth: 3,
			barGap: 2,
			barRadius: 3,
			normalize: true,
			dragToSeek: true
		});
		ws.on('ready', (d) => { ready = true; total = d; });
		ws.on('error', () => (failed = true));
		ws.on('timeupdate', (t) => (time = t));
		ws.on('play', () => { playing = true; onplay?.(); });
		ws.on('pause', () => (playing = false));
		ws.on('finish', () => (playing = false));
		// Repaint in the right colors when the system switches light/dark.
		const mq = matchMedia('(prefers-color-scheme: dark)');
		const recolor = () => ws?.setOptions({ waveColor: css('--line'), progressColor: css('--accent'), cursorColor: css('--accent') });
		mq.addEventListener('change', recolor);
		return () => { mq.removeEventListener('change', recolor); ws?.destroy(); ws = null; };
	});

	export function seekTo(t, play = true) {
		if (!ws) return;
		ws.setTime(t);
		if (play && !ws.isPlaying()) ws.play();
	}

	export function toggle() {
		ws?.playPause();
	}

	function key(e) {
		if (!ws) return;
		if (e.key === 'ArrowRight') { e.preventDefault(); ws.setTime(Math.min(total, ws.getCurrentTime() + 5)); }
		if (e.key === 'ArrowLeft') { e.preventDefault(); ws.setTime(Math.max(0, ws.getCurrentTime() - 5)); }
	}
</script>

<div class="player">
	<button class="play" onclick={toggle} disabled={!ready} aria-label={playing ? 'Pause' : 'Play'}>
		<Icon name={playing ? 'pause' : 'play'} size={26} />
	</button>
	<div class="wave-wrap">
		<div class="wave" bind:this={container} role="slider" tabindex="0" aria-label="Position in recording"
			aria-valuemin="0" aria-valuemax={Math.round(total)} aria-valuenow={Math.round(time)}
			aria-valuetext="{fmtDuration(time)} of {fmtDuration(total)}" onkeydown={key}></div>
		{#if failed}<p class="bad small">Couldn't load the audio.</p>{/if}
		<div class="times"><span>{fmtDuration(time)}</span><span>{fmtDuration(total)}</span></div>
	</div>
</div>

<style>
	.player { display: flex; gap: 14px; align-items: center; }
	.play {
		flex: none; width: 58px; height: 58px; border-radius: 50%; border: 0; cursor: pointer;
		background: var(--accent); color: var(--accent-fg); display: grid; place-items: center;
	}
	.play:disabled { opacity: 0.5; cursor: default; }
	.wave-wrap { flex: 1; min-width: 0; }
	.wave { min-height: 72px; cursor: pointer; border-radius: 6px; }
	.times { display: flex; justify-content: space-between; font-size: 0.8rem; color: var(--muted); font-variant-numeric: tabular-nums; }
</style>
