<script>
	// Edit a greeting or prompt: what it's called, when it plays, how often.
	import { untrack } from 'svelte';
	import { api } from '#lib/api.js';
	import { toast, toastError } from '#lib/session.svelte.js';
	import Icon from './Icon.svelte';

	let { clip, onsaved, ondeleted, oncancel } = $props();

	const year = new Date().getFullYear();
	const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
	const PRIORITIES = [
		{ value: 0, label: 'Normal' },
		{ value: 10, label: 'Special (holidays, birthdays): replaces normal ones' },
		{ value: 100, label: 'Top: replaces everything while it applies' }
	];

	function toInputs(r) {
		if (r.start.length === 10) return { start: r.start, end: r.end, yearly: false };
		const start = `${year}-${r.start}`;
		let end = `${year}-${r.end}`;
		if (r.end < r.start) end = `${year + 1}-${r.end}`; // wraps New Year
		return { start, end, yearly: true };
	}

	// The form starts as a copy of the clip (the parent re-creates this editor per clip).
	let f = $state(untrack(() => ({
		title: clip.title,
		text: clip.text || '',
		enabled: clip.enabled,
		priority: clip.priority,
		weight: clip.weight,
		dates: (clip.rules.dates || []).map(toInputs),
		weekdays: clip.rules.weekdays ? [...clip.rules.weekdays] : [],
		timed: !!clip.rules.time_from,
		time_from: clip.rules.time_from || '17:00',
		time_to: clip.rules.time_to || '21:00'
	})));
	let busy = $state(false);
	let error = $state('');
	let fileInput = $state();
	const customPriority = $derived(!PRIORITIES.some((p) => p.value === Number(f.priority)));

	function rules() {
		const out = {};
		const dates = f.dates.filter((d) => d.start && d.end)
			.map((d) => (d.yearly ? { start: d.start.slice(5), end: d.end.slice(5) } : { start: d.start, end: d.end }));
		if (dates.length) out.dates = dates;
		if (f.weekdays.length && f.weekdays.length < 7) out.weekdays = [...f.weekdays].sort();
		if (f.timed) { out.time_from = f.time_from; out.time_to = f.time_to; }
		return out;
	}

	async function save(e) {
		e.preventDefault();
		busy = true;
		error = '';
		try {
			const updated = await api(`/api/clips/${clip.id}`, { method: 'PATCH', body: {
				title: f.title, text: f.text, enabled: f.enabled, priority: Number(f.priority), weight: Number(f.weight), rules: rules() } });
			toast(updated.enabled ? 'Saved. The phone picks it up within a minute.' : 'Saved. It’s switched off, so the phone won’t play it yet.');
			onsaved?.(updated);
		} catch (err) {
			error = err.message;
		} finally {
			busy = false;
		}
	}

	async function replaceAudio(e) {
		const file = e.currentTarget.files?.[0];
		if (!file) return;
		busy = true;
		const form = new FormData();
		form.append('audio', file);
		try {
			const updated = await api(`/api/clips/${clip.id}/audio`, { method: 'PUT', form });
			toast('Audio replaced.');
			onsaved?.(updated, { keepOpen: true });
		} catch (err) {
			toastError(err);
		} finally {
			busy = false;
			e.currentTarget.value = '';
		}
	}

	async function remove() {
		if (!confirm(`Delete “${clip.title}”? It moves to the archive’s trash folder.`)) return;
		try {
			await api(`/api/clips/${clip.id}`, { method: 'DELETE' });
			toast('Deleted.');
			ondeleted?.(clip);
		} catch (err) {
			toastError(err);
		}
	}

	function toggleDay(i) {
		f.weekdays = f.weekdays.includes(i) ? f.weekdays.filter((d) => d !== i) : [...f.weekdays, i];
	}
</script>

<form class="editor stack" onsubmit={save}>
	<label class="field"><span>Name</span><input bind:value={f.title} required maxlength="200" /></label>
	{#if clip.kind === 'prompt'}
		<label class="field"><span>The question, in words</span>
			<input bind:value={f.text} placeholder="What’s been on your mind lately?" />
			<span class="hint">Shown next to everyone’s answers.</span></label>
	{/if}

	<label class="check"><input type="checkbox" bind:checked={f.enabled} /> On: the phone can play this</label>

	<fieldset>
		<legend>When</legend>
		{#each f.dates as d, i}
			<div class="daterow">
				<input type="date" bind:value={d.start} aria-label="From" required />
				<span>to</span>
				<input type="date" bind:value={d.end} aria-label="Until" required />
				<label class="check small"><input type="checkbox" bind:checked={d.yearly} /> every year</label>
				<button type="button" class="btn small ghost" onclick={() => f.dates.splice(i, 1)} aria-label="Remove dates"><Icon name="x" size={16} /></button>
			</div>
		{/each}
		<button type="button" class="btn small" onclick={() => f.dates.push({ start: `${year}-12-01`, end: `${year}-12-31`, yearly: true })}>
			+ Dates</button>
		{#if !f.dates.length}<span class="hint inline">No dates: any day of the year.</span>{/if}

		<div class="days" role="group" aria-label="Days of the week">
			{#each WEEKDAYS as day, i}
				<button type="button" class="day" class:on={f.weekdays.includes(i)} aria-pressed={f.weekdays.includes(i)} onclick={() => toggleDay(i)}>{day}</button>
			{/each}
			<span class="hint inline">{f.weekdays.length === 0 || f.weekdays.length === 7 ? 'Every day of the week' : ''}</span>
		</div>

		<label class="check"><input type="checkbox" bind:checked={f.timed} /> Only between</label>
		{#if f.timed}
			<div class="daterow">
				<input type="time" bind:value={f.time_from} aria-label="From" required /> <span>and</span>
				<input type="time" bind:value={f.time_to} aria-label="Until" required />
			</div>
		{/if}
	</fieldset>

	<div class="two">
		<label class="field"><span>Priority</span>
			<select bind:value={f.priority}>
				{#each PRIORITIES as p}<option value={p.value}>{p.label}</option>{/each}
				{#if customPriority}<option value={f.priority}>Custom ({f.priority})</option>{/if}
			</select>
			<span class="hint">When several could play, only the highest priority ones are used.</span>
		</label>
		<label class="field"><span>Share of plays</span>
			<input type="number" bind:value={f.weight} min="0" max="1000" step="0.5" />
			<span class="hint">Among equals: 2 plays twice as often as 1. 0 never plays.</span>
		</label>
	</div>

	{#if error}<p class="bad small" role="alert">{error}</p>{/if}
	<div class="row">
		<button class="btn primary" disabled={busy}>Save</button>
		<button type="button" class="btn" onclick={oncancel}>Cancel</button>
		<span class="spacer"></span>
		{#if !clip.builtin}
			<input type="file" accept="audio/*,video/*" bind:this={fileInput} onchange={replaceAudio} hidden />
			<button type="button" class="btn small ghost" onclick={() => fileInput.click()} disabled={busy}><Icon name="upload" size={16} /> Replace audio</button>
			<button type="button" class="btn small danger" onclick={remove}><Icon name="trash" size={16} /> Delete</button>
		{/if}
	</div>
</form>

<style>
	.editor { border-top: 1px solid var(--line); margin-top: 14px; padding-top: 14px; }
	fieldset { border: 1px solid var(--line); border-radius: 12px; padding: 12px; margin: 0; display: flex; flex-direction: column; gap: 10px; align-items: flex-start; }
	legend { font-size: 0.875rem; color: var(--muted); font-weight: 500; padding: 0 4px; }
	.daterow { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
	.daterow input { width: auto !important; }
	.days { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
	.day { border: 1px solid var(--line); background: transparent; color: var(--muted); border-radius: 8px; padding: 5px 9px; cursor: pointer; font: inherit; font-size: 0.85rem; }
	.day.on { background: var(--fg); color: var(--bg); border-color: var(--fg); }
	.inline { margin-left: 6px; }
	.two { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
	@media (max-width: 560px) { .two { grid-template-columns: 1fr; } }
</style>
