<script>
	import { page } from '$app/state';
	import { api, qs } from '#lib/api.js';
	import { toast, toastError } from '#lib/session.svelte.js';
	import { fmtDuration, plural } from '#lib/format.js';
	import ClipEditor from '#lib/components/ClipEditor.svelte';
	import Recorder from '#lib/components/Recorder.svelte';
	import Icon from '#lib/components/Icon.svelte';

	const tab = $derived(page.url.searchParams.get('tab') === 'prompts' ? 'prompt' : 'greeting');
	const listKey = $derived(tab === 'prompt' ? 'prompts' : 'greetings');
	let data = $state(null);
	let at = $state('');
	let open = $state(null); // id of the clip being edited
	let adding = $state(false);
	let source = $state('record');
	let newTitle = $state('');
	let newText = $state('');
	let blob = $state(null);
	let file = $state(null);
	let uploading = $state(false);
	let preview = null;
	let previewing = $state(null);

	async function load() {
		try {
			// Local wall-clock time, as typed: rules are about the phone's local time.
			data = await api('/api/clips' + qs({ at }));
		} catch (e) {
			toastError(e);
		}
	}

	$effect(() => {
		at;
		load();
	});

	$effect(() => {
		tab;
		open = null;
		adding = false;
	});

	const clips = $derived(data?.[listKey] || []);
	const activeNow = $derived(clips.filter((c) => c.active_chance));

	function play(c) {
		if (!preview) {
			preview = new Audio();
			preview.addEventListener('ended', () => (previewing = null));
			preview.addEventListener('pause', () => (previewing = null));
		}
		if (previewing === c.id) {
			preview.pause();
			return;
		}
		preview.src = c.audio_url;
		preview.play();
		previewing = c.id;
	}

	async function toggleEnabled(c) {
		try {
			const updated = await api(`/api/clips/${c.id}`, { method: 'PATCH', body: { enabled: !c.enabled } });
			toast(updated.enabled ? `“${c.title}” is on.` : `“${c.title}” is off.`);
			load();
		} catch (e) {
			toastError(e);
		}
	}

	async function create(e) {
		e.preventDefault();
		const audio = source === 'record' ? blob : file;
		if (!audio) {
			toast(source === 'record' ? 'Record something first.' : 'Choose an audio file first.', { kind: 'err' });
			return;
		}
		uploading = true;
		const form = new FormData();
		form.append('kind', tab);
		form.append('title', newTitle);
		form.append('text', newText);
		const ext = (audio.type || '').includes('mp4') ? 'm4a' : (audio.type || '').includes('ogg') ? 'ogg' : 'webm';
		form.append('audio', audio, audio.name || `recording.${ext}`);
		try {
			const clip = await api('/api/clips', { method: 'POST', form });
			toast('Added. Set when it should play, then switch it on.');
			adding = false;
			newTitle = newText = '';
			blob = file = null;
			await load();
			open = clip.id;
		} catch (err) {
			toastError(err);
		} finally {
			uploading = false;
		}
	}

	function chance(c) {
		return c.active_chance >= 0.995 ? 'Plays now' : `Plays now · ${Math.round(c.active_chance * 100)}% of calls`;
	}
</script>

<svelte:head><title>Greetings · PhoneHome</title></svelte:head>

<section class="page-head">
	<div>
		<h1>Greetings &amp; prompts</h1>
		<p class="muted">{tab === 'greeting'
			? 'What callers hear when they pick up, before the beep.'
			: 'Questions the phone asks when it rings on its own.'}</p>
	</div>
	<nav class="pill-nav" aria-label="Kind">
		<a href="/greetings" class:active={tab === 'greeting'}>Greetings</a>
		<a href="/greetings?tab=prompts" class:active={tab === 'prompt'}>Prompts</a>
	</nav>
</section>

{#if data}
	<section class="card now">
		<div class="now-head">
			<Icon name={tab === 'greeting' ? 'phone' : 'bell'} size={22} />
			<div>
				<strong>{at ? 'At that time' : 'Right now'}, {tab === 'greeting' ? 'picking up plays' : 'a ring asks'}:</strong>
				{#if activeNow.length === 0}
					<span class="muted">{tab === 'greeting' ? 'the built-in chime' : 'nothing (no prompt is on), so it plays a memory instead'}</span>
				{:else if activeNow.length === 1}
					<span>{activeNow[0].title}</span>
				{:else}
					<span>one of {activeNow.map((c) => `${c.title} (${Math.round(c.active_chance * 100)}%)`).join(', ')}</span>
				{/if}
			</div>
		</div>
		<label class="when small muted">Check another time
			<input type="datetime-local" bind:value={at} />
			{#if at}<button class="btn small ghost" onclick={() => (at = '')}>Now</button>{/if}
		</label>
	</section>

	{#each clips as c (c.id)}
		<article class="card clip" class:off={!c.enabled}>
			<div class="clip-head">
				{#if c.audio_url}
					<button class="play" onclick={() => play(c)} aria-label={previewing === c.id ? 'Stop' : `Play ${c.title}`}>
						<Icon name={previewing === c.id ? 'stop' : 'play'} size={18} />
					</button>
				{:else}
					<span class="play builtin" title="A sound built into the phone"><Icon name="bell" size={18} /></span>
				{/if}
				<div class="info">
					<h3>{c.title}</h3>
					<p class="muted small">
						{c.rules_summary}{c.duration_s ? ` · ${fmtDuration(c.duration_s)}` : ''}
						{#if c.priority} · priority {c.priority}{/if}
						{#if c.weight !== 1} · share {c.weight}{/if}
						{#if c.builtin} · built in{/if}
					</p>
					{#if c.text}<p class="question">“{c.text}”</p>{/if}
					<div class="chips">
						{#if c.active_chance}<span class="chip live">{chance(c)}</span>{/if}
						{#if tab === 'prompt'}
							<a class="chip" href="/search?prompt={encodeURIComponent(c.id)}">{plural(c.answers || 0, 'answer')}</a>
						{/if}
					</div>
				</div>
				<label class="switch" title={c.enabled ? 'On' : 'Off'}>
					<input type="checkbox" checked={c.enabled} onchange={() => toggleEnabled(c)} aria-label="{c.title} on" />
					<span></span>
				</label>
			</div>
			{#if open === c.id}
				<ClipEditor clip={c} oncancel={() => (open = null)} ondeleted={() => { open = null; load(); }}
					onsaved={(u, o) => { if (!o?.keepOpen) open = null; load(); }} />
			{:else}
				<button class="btn small ghost edit" onclick={() => (open = c.id)}><Icon name="edit" size={16} /> Edit</button>
			{/if}
		</article>
	{/each}

	{#if !clips.length && tab === 'prompt'}
		<div class="card empty">
			<h2>No prompts yet</h2>
			<p class="muted">Record a question like “What’s your favorite memory from this year?” When the phone rings
				and someone answers, it asks the question, then records their answer.</p>
		</div>
	{/if}

	{#if adding}
		<form class="card stack" onsubmit={create}>
			<h2>New {tab}</h2>
			<label class="field"><span>Name</span>
				<input bind:value={newTitle} placeholder={tab === 'greeting' ? 'Christmas greeting' : 'Favorite memory'} /></label>
			{#if tab === 'prompt'}
				<label class="field"><span>The question, in words</span>
					<input bind:value={newText} placeholder="What’s your favorite memory from this year?" /></label>
			{/if}
			<div class="pill-nav" role="tablist">
				<button type="button" role="tab" class:active={source === 'record'} aria-selected={source === 'record'} onclick={() => (source = 'record')}>Record</button>
				<button type="button" role="tab" class:active={source === 'upload'} aria-selected={source === 'upload'} onclick={() => (source = 'upload')}>Upload a file</button>
			</div>
			{#if source === 'record'}
				<Recorder onrecorded={(b) => (blob = b)} />
			{:else}
				<input type="file" accept="audio/*,video/*" onchange={(e) => (file = e.currentTarget.files?.[0] || null)} />
				<p class="hint">Any audio file: voice memos (m4a), mp3, wav… It’s trimmed and leveled to the same volume as everything else.</p>
			{/if}
			<div class="row">
				<button class="btn primary" disabled={uploading}>{uploading ? 'Converting…' : 'Add'}</button>
				<button type="button" class="btn" onclick={() => (adding = false)}>Cancel</button>
			</div>
		</form>
	{:else}
		<button class="btn primary add" onclick={() => (adding = true)}><Icon name="mic" size={18} /> New {tab}</button>
	{/if}
{:else}
	<p class="muted">Loading…</p>
{/if}

<style>
	.now { background: var(--accent-soft); border-color: transparent; }
	.now-head { display: flex; gap: 12px; align-items: flex-start; }
	.now-head :global(svg) { color: var(--accent); flex: none; margin-top: 2px; }
	.now-head div { display: flex; flex-direction: column; }
	.when { display: flex; align-items: center; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
	.when input { width: auto !important; padding: 4px 8px !important; font-size: 0.9rem !important; background: var(--card) !important; }
	.clip.off .info h3, .clip.off .question { color: var(--muted); }
	.clip-head { display: flex; gap: 14px; align-items: flex-start; }
	.play {
		flex: none; width: 40px; height: 40px; border-radius: 50%; border: 0; display: grid; place-items: center;
		background: var(--accent-soft); color: var(--accent); cursor: pointer;
	}
	.play.builtin { background: var(--chip); color: var(--muted); cursor: default; }
	.info { flex: 1; min-width: 0; }
	.info h3 { margin: 0; }
	.info p { margin: 2px 0 0; }
	.question { font-style: italic; }
	.chips { margin-top: 8px; }
	.chip.live { background: color-mix(in srgb, var(--ok) 18%, var(--card)); }
	.edit { margin-top: 8px; margin-left: 46px; }
	.add { margin-top: 6px; }
	.switch { position: relative; width: 44px; height: 26px; flex: none; cursor: pointer; }
	.switch input { position: absolute; opacity: 0; inset: 0; margin: 0; cursor: pointer; width: 100%; height: 100%; }
	.switch span { position: absolute; inset: 0; border-radius: 13px; background: var(--line); transition: background 0.15s; pointer-events: none; }
	.switch span::after {
		content: ''; position: absolute; top: 3px; left: 3px; width: 20px; height: 20px; border-radius: 50%;
		background: #fff; box-shadow: 0 1px 2px rgb(0 0 0 / 0.25); transition: transform 0.15s;
	}
	.switch input:checked + span { background: var(--ok); }
	.switch input:checked + span::after { transform: translateX(18px); }
	.switch input:focus-visible + span { outline: 2px solid var(--accent); outline-offset: 2px; }
</style>
