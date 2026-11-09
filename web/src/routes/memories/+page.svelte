<script>
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { api, qs } from '#lib/api.js';
	import { toastError } from '#lib/session.svelte.js';
	import { fmtMonthDay, plural } from '#lib/format.js';
	import MessageCard from '#lib/components/MessageCard.svelte';
	import Icon from '#lib/components/Icon.svelte';

	const date = $derived(page.url.searchParams.get('date') || '');
	let data = $state(null);

	$effect(() => {
		data = null;
		api('/api/on-this-day' + qs({ date })).then((r) => (data = r)).catch(toastError);
	});

	function shift(days) {
		const [m, d] = (data?.date || '01-01').split('-').map(Number);
		const dt = new Date(Date.UTC(2024, m - 1, d + days)); // a leap year, so Feb 29 exists
		const mmdd = `${String(dt.getUTCMonth() + 1).padStart(2, '0')}-${String(dt.getUTCDate()).padStart(2, '0')}`;
		goto('/memories' + qs({ date: mmdd }), { replaceState: true, noScroll: true });
	}

	const count = $derived(data ? data.years.reduce((n, y) => n + y.messages.length, 0) : 0);
	const pickerValue = $derived(data ? `2024-${data.date}` : '');
</script>

<svelte:head><title>Memories · PhoneHome</title></svelte:head>

<section class="page-head">
	<div>
		<h1>{data?.today ? 'On this day' : data ? fmtMonthDay(data.date) : 'Memories'}</h1>
		<p class="muted">{data?.today ? fmtMonthDay(data.date) + ' in years past' : 'Messages from this date in every year'}</p>
	</div>
	<div class="row">
		<button class="btn small" onclick={() => shift(-1)} aria-label="Previous day"><Icon name="back" size={16} /></button>
		<input type="date" class="picker" value={pickerValue} aria-label="Pick a date"
			onchange={(e) => e.currentTarget.value && goto('/memories' + qs({ date: e.currentTarget.value.slice(5) }), { replaceState: true })} />
		<button class="btn small" onclick={() => shift(1)} aria-label="Next day"><Icon name="chevron" size={16} /></button>
		{#if date}<a class="btn small ghost" href="/memories">Today</a>{/if}
	</div>
</section>

{#if !data}
	<p class="muted" aria-busy="true">Loading…</p>
{:else if !count}
	<div class="card empty">
		<Icon name="calendar" size={34} />
		<h2>Nothing from {fmtMonthDay(data.date)} yet</h2>
		<p class="muted">{data.today
			? 'Once the phone has been on the wall for a year, messages from this date in earlier years show up here.'
			: 'Nobody left a message on this date in any year.'}</p>
	</div>
{:else}
	{#each data.years as y (y.year)}
		<h2 class="section-label">{y.year} · {y.years_ago > 0 ? plural(y.years_ago, 'year') + ' ago' : 'this year'}</h2>
		{#each y.messages as m (m.id)}<MessageCard {m} />{/each}
	{/each}
{/if}

<style>
	.picker { width: auto !important; padding: 5px 10px !important; font-size: 0.9rem !important; background: var(--card) !important; }
	.empty :global(svg) { color: var(--muted); margin-bottom: 8px; }
</style>
