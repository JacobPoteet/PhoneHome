<script>
	// Record a greeting in the browser. Browsers only allow the microphone on
	// HTTPS or on localhost, so on a plain-HTTP LAN address this explains why
	// and offers the upload route instead.
	import Icon from './Icon.svelte';
	import { fmtDuration } from '#lib/format.js';

	let { onrecorded } = $props();

	const supported = typeof window !== 'undefined' && window.isSecureContext && !!navigator.mediaDevices?.getUserMedia
		&& typeof MediaRecorder !== 'undefined';
	let state = $state('idle'); // idle | recording | done
	let error = $state('');
	let elapsed = $state(0);
	let level = $state(0);
	let previewUrl = $state('');
	let recorder, stream, chunks = [], ctx, raf, started;

	async function start() {
		error = '';
		try {
			stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: true } });
		} catch (e) {
			error = e.name === 'NotAllowedError' ? 'Microphone permission was denied.' : `No microphone: ${e.message}`;
			return;
		}
		chunks = [];
		const type = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/ogg;codecs=opus'].find((t) => MediaRecorder.isTypeSupported(t));
		recorder = new MediaRecorder(stream, type ? { mimeType: type } : undefined);
		recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
		recorder.onstop = () => {
			const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
			if (previewUrl) URL.revokeObjectURL(previewUrl);
			previewUrl = URL.createObjectURL(blob);
			state = 'done';
			onrecorded?.(blob);
		};
		// A simple level meter, so you can see the mic is hearing you.
		ctx = new AudioContext();
		const analyser = ctx.createAnalyser();
		analyser.fftSize = 512;
		ctx.createMediaStreamSource(stream).connect(analyser);
		const buf = new Float32Array(analyser.fftSize);
		started = performance.now();
		const tick = () => {
			analyser.getFloatTimeDomainData(buf);
			level = Math.min(1, Math.sqrt(buf.reduce((s, v) => s + v * v, 0) / buf.length) * 4);
			elapsed = (performance.now() - started) / 1000;
			raf = requestAnimationFrame(tick);
		};
		tick();
		recorder.start();
		state = 'recording';
	}

	function stop() {
		recorder?.stop();
		stream?.getTracks().forEach((t) => t.stop());
		cancelAnimationFrame(raf);
		ctx?.close();
		level = 0;
	}

	function reset() {
		state = 'idle';
		onrecorded?.(null);
	}

	$effect(() => () => { stop(); if (previewUrl) URL.revokeObjectURL(previewUrl); });
</script>

{#if !supported}
	<p class="hint">Recording in the browser needs a secure address: open PhoneHome over HTTPS, or at
		<code>localhost</code>. Until then, record on your phone’s voice memo app and upload the file.</p>
{:else if state === 'idle'}
	<button type="button" class="btn" onclick={start}><Icon name="mic" size={18} /> Start recording</button>
{:else if state === 'recording'}
	<div class="rec">
		<span class="dotrec" aria-hidden="true"></span>
		<span class="time">{fmtDuration(elapsed)}</span>
		<div class="meter" aria-hidden="true"><div style:width="{level * 100}%"></div></div>
		<button type="button" class="btn primary" onclick={stop}><Icon name="stop" size={16} /> Stop</button>
	</div>
{:else}
	<div class="row">
		<audio controls src={previewUrl}></audio>
		<button type="button" class="btn small ghost" onclick={reset}>Record again</button>
	</div>
{/if}
{#if error}<p class="bad small">{error}</p>{/if}

<style>
	.rec { display: flex; align-items: center; gap: 12px; }
	.dotrec { width: 12px; height: 12px; border-radius: 50%; background: var(--bad); animation: pulse 1s infinite; }
	.time { font-variant-numeric: tabular-nums; min-width: 3em; }
	.meter { flex: 1; height: 8px; background: var(--chip); border-radius: 4px; overflow: hidden; }
	.meter div { height: 100%; background: var(--ok); transition: width 0.05s; }
	audio { height: 40px; max-width: 100%; }
	@keyframes pulse { 50% { opacity: 0.3; } }
	@media (prefers-reduced-motion: reduce) { .dotrec { animation: none; } }
</style>
