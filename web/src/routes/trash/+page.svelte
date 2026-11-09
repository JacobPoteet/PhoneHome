<script>
	import { api } from '#lib/api.js';
	import { toast, toastError, refreshStats } from '#lib/session.svelte.js';
	import { fmtShortDate, fmtTime, fmtDuration, messageTitle } from '#lib/format.js';
	import Icon from '#lib/components/Icon.svelte';

	let msgs = $state(null);
	let days = $state(30);

	async function load() {
		try {
			const [r, s] = await Promise.all([api('/api/messages?trashed=true&limit=200'), api('/api/system')]);
			msgs = r.messages;
			days = s.trash_days;
		} catch (e) {
			toastError(e);
		}
	}
	$effect(() => { load(); });

	async function restore(m) {
		try {
			await api(`/api/messages/${m.id}/restore`, { method: 'POST' });
			msgs = msgs.filter((x) => x.id !== m.id);
			refreshStats();
			toast('Restored.');
		} catch (e) {
			toastError(e);
		}
	}
</script>

<svelte:head><title>Trash · PhoneHome</title></svelte:head>

<a class="back muted" href="/"><Icon name="back" size={18} /> Messages</a>
<h1>Trash</h1>
<p class="muted">Deleted messages stay here for {days} days, then they’re removed for good.</p>

{#if msgs === null}
	<p class="muted">Loading…</p>
{:else if !msgs.length}
	<div class="card empty"><p class="muted">The trash is empty.</p></div>
{:else}
	{#each msgs as m (m.id)}
		<div class="card item">
			<div>
				<a href="/m/{m.id}"><strong>{messageTitle(m)}</strong></a>
				<p class="muted small">{fmtShortDate(m.recorded_at)} · {fmtTime(m.recorded_at)} · {fmtDuration(m.duration_s)} · deleted {fmtShortDate(m.deleted_at)}</p>
			</div>
			<button class="btn small" onclick={() => restore(m)}>Restore</button>
		</div>
	{/each}
{/if}

<style>
	.back { display: inline-flex; align-items: center; gap: 4px; text-decoration: none; margin-bottom: 12px; }
	.item { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
	.item a { color: var(--fg); text-decoration: none; }
	.item p { margin: 2px 0 0; }
</style>
