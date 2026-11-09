<script>
	// Shows the transcript. The computer's version follows along as the
	// message plays (click any word to jump there); a person's correction is
	// shown as plain text, since its words no longer line up with the timing.
	import { api } from '#lib/api.js';
	import { toast, toastError } from '#lib/session.svelte.js';
	import Icon from './Icon.svelte';

	let { msg = $bindable(), time = 0, onseek, available = true } = $props();

	const t = $derived(msg.transcript);
	let editing = $state(false);
	let draft = $state('');
	let saving = $state(false);
	let showMachine = $state(false);

	const words = $derived(t.segments.flatMap((s, si) =>
		(s.words?.length ? s.words : [{ start: s.start, end: s.end, text: ' ' + s.text }]).map((w) => ({ ...w, si }))));
	const activeIndex = $derived.by(() => {
		for (let i = words.length - 1; i >= 0; i--) if (time >= words[i].start - 0.05) return time <= words[i].end + 0.6 ? i : -1;
		return -1;
	});
	const followAlong = $derived(words.length > 0 && (!t.edited || showMachine));

	function startEdit() {
		draft = t.edited ?? t.machine_text ?? '';
		editing = true;
	}

	async function save(text) {
		saving = true;
		try {
			msg.transcript = await api(`/api/messages/${msg.id}/transcript`, { method: 'PUT', body: { text } });
			msg.transcript_state = msg.transcript.state;
			editing = false;
			toast(text ? 'Transcript saved. The original is kept too.' : 'Back to the computer’s transcript.');
		} catch (e) {
			toastError(e);
		} finally {
			saving = false;
		}
	}

	async function retranscribe() {
		try {
			await api(`/api/messages/${msg.id}/transcribe`, { method: 'POST' });
			msg.transcript_state = 'pending';
			msg.transcript = { ...msg.transcript, state: t.edited ? 'edited' : 'pending' };
			toast('Transcribing again. Your corrections are kept.');
		} catch (e) {
			toastError(e);
		}
	}
</script>

<section class="card transcript">
	<div class="head">
		<h2>Transcript</h2>
		{#if t.edited && !editing}<span class="chip">Corrected by hand</span>{/if}
		<span class="spacer"></span>
		{#if !editing && (t.machine_text || t.edited)}
			<button class="btn small ghost" onclick={startEdit}><Icon name="edit" size={16} /> Correct</button>
		{/if}
	</div>

	{#if editing}
		<textarea bind:value={draft} rows="6" aria-label="Transcript text"></textarea>
		<div class="row">
			<button class="btn primary small" onclick={() => save(draft)} disabled={saving}>Save</button>
			<button class="btn small" onclick={() => (editing = false)}>Cancel</button>
			<span class="spacer"></span>
			{#if t.edited}<button class="btn small ghost" onclick={() => save('')} disabled={saving}>Undo my corrections</button>{/if}
		</div>
		<p class="hint">The computer’s version is kept separately, so a better model later never overwrites your fixes.</p>
	{:else if followAlong}
		<p class="text follow">
			{#each words as w, i}{i && /^\s/.test(w.text) ? ' ' : ''}<button class="w" class:on={i === activeIndex} tabindex="-1"
				onclick={() => onseek?.(w.start)}>{w.text.trim()}</button>{/each}
		</p>
	{:else if t.edited}
		<p class="text">{t.edited}</p>
	{:else if t.state === 'pending' || msg.transcript_state === 'pending'}
		<p class="muted">Transcribing… This page updates when it’s ready.</p>
	{:else if t.state === 'failed'}
		<p class="muted">Transcription failed. <button class="link" onclick={retranscribe}>Try again</button></p>
	{:else if !available}
		<p class="muted small">Transcription is off on this server. See Settings.</p>
	{:else}
		<p class="muted">No transcript yet. <button class="link" onclick={retranscribe}>Transcribe now</button></p>
	{/if}

	{#if !editing && (t.machine_text || t.edited)}
		<div class="foot">
			{#if t.edited && words.length}
				<button class="link" onclick={() => (showMachine = !showMachine)}>
					{showMachine ? 'Show my corrected version' : 'Follow along with the computer’s version'}</button>
			{/if}
			<span class="spacer"></span>
			{#if t.model}<span>{t.model}</span>{/if}
			{#if available}<button class="link" onclick={retranscribe}>Transcribe again</button>{/if}
		</div>
	{/if}
</section>

<style>
	.head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
	.head h2 { margin: 0; }
	.text { margin: 0; font-size: 1.05rem; line-height: 1.7; white-space: pre-wrap; }
	.follow { white-space: normal; }
	.w {
		font: inherit; color: inherit; background: none; border: 0; padding: 0; margin: 0; cursor: pointer;
		border-radius: 4px; transition: background 0.12s;
	}
	.w:hover { background: var(--chip); }
	.w.on { background: var(--mark); }
	textarea { font-size: 1rem; line-height: 1.6; margin-bottom: 10px; }
	.foot { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin-top: 12px; font-size: 0.8rem; color: var(--muted); }
	.link { background: none; border: 0; padding: 0; color: var(--accent); cursor: pointer; font: inherit; text-decoration: underline; }
	.hint { margin-top: 8px; }
</style>
