<script>
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { api } from '#lib/api.js';
	import { toast, toastError, refreshStats } from '#lib/session.svelte.js';
	import { stopShared, markHeard } from '#lib/player.svelte.js';
	import { fmtDate, fmtTime, fmtDuration, fmtShortDate, fmtBytes, KIND_LABELS, messageTitle } from '#lib/format.js';
	import Waveform from '#lib/components/Waveform.svelte';
	import Transcript from '#lib/components/Transcript.svelte';
	import ChipInput from '#lib/components/ChipInput.svelte';
	import Icon from '#lib/components/Icon.svelte';

	const id = $derived(page.params.id);
	let msg = $state(null);
	let notFound = $state(false);
	let form = $state(null);
	let saving = $state(false);
	let time = $state(0);
	let playing = $state(false);
	let wave = $state();
	let facets = $state({ people: [], tags: [] });

	async function load() {
		try {
			msg = await api(`/api/messages/${id}`);
			form = { title: msg.title, notes: msg.notes, people: [...msg.people], tags: [...msg.tags],
				favorite: msg.favorite, replay_eligible: msg.replay_eligible };
		} catch (e) {
			if (e.status === 404) notFound = true;
			else toastError(e);
		}
	}

	$effect(() => {
		id;
		msg = null;
		notFound = false;
		load();
		api('/api/search?limit=1').then((r) => (facets = {
			people: r.facets.people.map((f) => f.value), tags: r.facets.tags.map((f) => f.value) })).catch(() => {});
	});

	// While a transcript is being made, check back every few seconds.
	$effect(() => {
		if (msg?.transcript_state !== 'pending') return;
		const t = setInterval(async () => {
			try {
				const fresh = await api(`/api/messages/${id}`);
				if (fresh.transcript_state !== 'pending') {
					msg.transcript = fresh.transcript;
					msg.transcript_state = fresh.transcript_state;
					msg.files = fresh.files;
				}
			} catch { /* keep trying */ }
		}, 5000);
		return () => clearInterval(t);
	});

	const dirty = $derived(msg && form && JSON.stringify(form) !== JSON.stringify({
		title: msg.title, notes: msg.notes, people: msg.people, tags: msg.tags,
		favorite: msg.favorite, replay_eligible: msg.replay_eligible }));

	async function save(e) {
		e?.preventDefault();
		saving = true;
		try {
			const r = await api(`/api/messages/${id}`, { method: 'PATCH', body: form });
			Object.assign(msg, r);
			form = { title: r.title, notes: r.notes, people: [...r.people], tags: [...r.tags],
				favorite: r.favorite, replay_eligible: r.replay_eligible };
			toast('Saved.');
		} catch (err) {
			toastError(err);
		} finally {
			saving = false;
		}
	}

	async function trash() {
		try {
			const r = await api(`/api/messages/${id}`, { method: 'DELETE' });
			refreshStats();
			toast(`Moved to the trash. It can be restored for ${r.trash_days} days.`, {
				timeout: 9000, action: { label: 'Undo', run: () => restore(true) } });
			goto('/');
		} catch (e) {
			toastError(e);
		}
	}

	async function restore(fromUndo = false) {
		try {
			await api(`/api/messages/${id}/restore`, { method: 'POST' });
			refreshStats();
			if (fromUndo) goto(`/m/${id}`);
			else { await load(); toast('Restored.'); }
		} catch (e) {
			toastError(e);
		}
	}

	function onPlay() {
		stopShared();
		markHeard(msg);
	}

	const hasTranscriptFile = $derived(msg?.files?.some((f) => f.name.endsWith('.txt')));
</script>

<svelte:head><title>{msg ? messageTitle(msg) : 'Message'} · PhoneHome</title></svelte:head>

<a class="back muted" href="/"><Icon name="back" size={18} /> Messages</a>

{#if notFound}
	<div class="card empty"><h2>Message not found</h2><p class="muted">It may have been deleted for good.</p></div>
{:else if !msg}
	<p class="muted" aria-busy="true">Loading…</p>
{:else}
	{#if msg.deleted_at}
		<div class="card banner">
			<span>This message is in the trash.</span>
			<button class="btn small" onclick={() => restore()}>Restore</button>
		</div>
	{/if}

	<article class="card hero">
		<h1>{messageTitle(msg)}</h1>
		<p class="meta">
			{msg.title ? fmtDate(msg.recorded_at) + ' at ' : ''}{fmtTime(msg.recorded_at)} · {fmtDuration(msg.duration_s)}
			{#if msg.prompt_title}
				· answering <a href="/search?prompt={encodeURIComponent(msg.prompt_id)}">“{msg.prompt_title}”</a>
			{:else if msg.kind !== 'guest'}
				· {KIND_LABELS[msg.kind] || msg.kind}
			{/if}
		</p>
		<Waveform bind:this={wave} src={msg.audio_url} duration={msg.duration_s} bind:time bind:playing onplay={onPlay} />
		{#if msg.reply_to_message}
			<p class="related">A reply after the phone played
				<a href="/m/{msg.reply_to_message.id}">{msg.reply_to_message.title || fmtShortDate(msg.reply_to_message.recorded_at)}</a>.</p>
		{/if}
		{#if msg.replies.length}
			<p class="related">Replies:
				{#each msg.replies as r, i}<a href="/m/{r.id}">{r.title || fmtShortDate(r.recorded_at)}</a>{i < msg.replies.length - 1 ? ', ' : ''}{/each}</p>
		{/if}
	</article>

	<Transcript bind:msg {time} onseek={(t) => wave?.seekTo(t)} available={msg.transcription_available} />

	<form class="card stack" onsubmit={save}>
		<h2>Details</h2>
		<label class="field"><span>Title</span>
			<input bind:value={form.title} placeholder="e.g. Grandma’s Thanksgiving call" /></label>
		<ChipInput bind:values={form.people} label="Who’s speaking" placeholder="Type a name, press Enter" suggestions={facets.people} />
		<ChipInput bind:values={form.tags} label="Tags" placeholder="thanksgiving, birthday…" suggestions={facets.tags} prefix="#" />
		<label class="field"><span>Notes</span><textarea bind:value={form.notes} rows="3"></textarea></label>
		<label class="check"><input type="checkbox" bind:checked={form.favorite} /> Favorite</label>
		<label class="check"><input type="checkbox" bind:checked={form.replay_eligible} /> The phone may play this when it rings (memory lane)</label>
		<div class="row">
			<button class="btn primary" disabled={!dirty || saving}>{saving ? 'Saving…' : 'Save'}</button>
			{#if dirty}<span class="muted small">Unsaved changes</span>{/if}
		</div>
	</form>

	<section class="card">
		<h2>Download</h2>
		<div class="row">
			<a class="btn small" href="{msg.flac_url}?download=1"><Icon name="download" size={16} /> Original (FLAC)</a>
			{#if msg.mp3_url}<a class="btn small" href="{msg.mp3_url}?download=1"><Icon name="download" size={16} /> MP3</a>{/if}
			{#if hasTranscriptFile}<a class="btn small" href="/media/{msg.id}.txt?download=1"><Icon name="download" size={16} /> Transcript</a>{/if}
		</div>
	</section>

	<details class="card">
		<summary>Technical details</summary>
		<dl class="kv">
			<dt>ID</dt><dd><code>{msg.id}</code></dd>
			<dt>Recorded</dt><dd>{msg.recorded_at}</dd>
			<dt>Received</dt><dd>{msg.details.received_at || '—'}</dd>
			<dt>Phone</dt><dd>{msg.details.device || '—'} (agent {msg.details.agent_version || '?'})</dd>
			<dt>Greeting</dt><dd>{msg.greeting_title || msg.greeting_id || '—'}</dd>
			<dt>Ended by</dt><dd>{msg.details.ended_by || '—'}{msg.details.recovered ? ' (recovered after a power cut)' : ''}</dd>
			{#if msg.details.levels}<dt>Levels</dt><dd>peak {msg.details.levels.peak_dbfs} dBFS · {msg.details.levels.voiced_s} s of speech</dd>{/if}
			<dt>Clock synced</dt><dd>{msg.details.clock_synced == null ? '—' : msg.details.clock_synced ? 'yes' : 'no'}</dd>
			<dt>Files</dt><dd>{#each msg.files as f}<code>{f.name}</code> <span class="muted">{fmtBytes(f.size)}</span><br />{/each}</dd>
			<dt>SHA-256</dt><dd><code>{msg.flac_sha256}</code></dd>
		</dl>
	</details>

	{#if !msg.deleted_at}
		<div class="row end"><button class="btn danger" onclick={trash}><Icon name="trash" size={16} /> Move to trash</button></div>
	{/if}
{/if}

<style>
	.back { display: inline-flex; align-items: center; gap: 4px; text-decoration: none; margin-bottom: 12px; font-size: 0.95rem; }
	.back:hover { color: var(--fg); }
	.hero h1 { margin-bottom: 2px; }
	.meta { color: var(--muted); margin: 0 0 16px; }
	.related { margin: 14px 0 0; font-size: 0.93rem; color: var(--muted); }
	.banner { display: flex; align-items: center; justify-content: space-between; gap: 12px;
		background: color-mix(in srgb, var(--bad) 10%, var(--card)); border-color: color-mix(in srgb, var(--bad) 40%, var(--line)); }
</style>
