<script>
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { api, qs } from '#lib/api.js';
	import { toastError } from '#lib/session.svelte.js';
	import { KIND_LABELS, plural } from '#lib/format.js';
	import MessageCard from '#lib/components/MessageCard.svelte';
	import Icon from '#lib/components/Icon.svelte';

	const PAGE = 30;
	const keys = ['q', 'year', 'person', 'tag', 'kind', 'favorite', 'prompt'];
	const params = $derived(Object.fromEntries(keys.map((k) => [k, page.url.searchParams.get(k) || ''])));

	let q = $state(page.url.searchParams.get('q') || '');
	let results = $state([]);
	let total = $state(0);
	let facets = $state({ years: [], people: [], tags: [], kinds: [] });
	let prompts = $state({});
	let loading = $state(false);
	let input;

	function setParam(changes) {
		const next = { ...params, ...changes };
		goto('/search' + qs(next), { replaceState: true, keepFocus: true, noScroll: true });
	}

	// Search as you type (after a short pause).
	let timer;
	function typed() {
		clearTimeout(timer);
		timer = setTimeout(() => setParam({ q: q.trim() }), 250);
	}

	async function run(more = false) {
		loading = true;
		try {
			const r = await api('/api/search' + qs({ ...params, limit: PAGE, offset: more ? results.length : 0 }));
			results = more ? [...results, ...r.results] : r.results;
			total = r.total;
			facets = r.facets;
		} catch (e) {
			toastError(e);
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		JSON.stringify(params); // rerun when the URL changes
		run();
	});

	$effect(() => {
		api('/api/clips?kind=prompt').then((r) => (prompts = Object.fromEntries(r.prompts.map((p) => [p.id, p.title])))).catch(() => {});
		input?.focus();
	});

	const filtered = $derived(keys.some((k) => k !== 'q' && params[k]));
</script>

<svelte:head><title>{params.q ? `“${params.q}”` : 'Search'} · PhoneHome</title></svelte:head>

<h1>Search</h1>
<form class="searchbox" role="search" onsubmit={(e) => { e.preventDefault(); clearTimeout(timer); setParam({ q: q.trim() }); }}>
	<Icon name="search" />
	<input bind:this={input} bind:value={q} oninput={typed} class="q" type="search" placeholder="Words people said, titles, notes, names…"
		aria-label="Search messages" enterkeyhint="search" />
</form>

<div class="filters">
	<select value={params.year} onchange={(e) => setParam({ year: e.currentTarget.value })} aria-label="Year">
		<option value="">Any year</option>
		{#each facets.years as y}<option value={y.value}>{y.value} ({y.count})</option>{/each}
	</select>
	<select value={params.person} onchange={(e) => setParam({ person: e.currentTarget.value })} aria-label="Person">
		<option value="">Anyone</option>
		{#each facets.people as p}<option value={p.value}>{p.value} ({p.count})</option>{/each}
	</select>
	<select value={params.tag} onchange={(e) => setParam({ tag: e.currentTarget.value })} aria-label="Tag">
		<option value="">Any tag</option>
		{#each facets.tags as t}<option value={t.value}>#{t.value} ({t.count})</option>{/each}
	</select>
	<select value={params.kind} onchange={(e) => setParam({ kind: e.currentTarget.value })} aria-label="Kind">
		<option value="">Any kind</option>
		{#each facets.kinds as k}<option value={k.value}>{KIND_LABELS[k.value] || k.value} ({k.count})</option>{/each}
	</select>
	<label class="check fav"><input type="checkbox" checked={!!params.favorite}
		onchange={(e) => setParam({ favorite: e.currentTarget.checked ? 'true' : '' })} /> Favorites</label>
</div>

{#if params.prompt}
	<p class="chips"><span class="chip">Answers to “{prompts[params.prompt] || params.prompt}”
		<button class="x" onclick={() => setParam({ prompt: '' })} aria-label="Clear">×</button></span></p>
{/if}

<p class="muted small summary" aria-live="polite">
	{#if loading && !results.length}Searching…
	{:else if params.q || filtered}{plural(total, 'message')}{params.q ? ` matching “${params.q}”` : ''}
		{#if filtered}· <button class="link" onclick={() => goto('/search' + qs({ q: params.q }), { replaceState: true })}>clear filters</button>{/if}
	{:else}Every message, newest first. Type to search what people said.{/if}
</p>

{#each results as m (m.id)}
	<MessageCard {m} />
{/each}

{#if !loading && !results.length && (params.q || filtered)}
	<div class="card empty"><h2>Nothing found</h2>
		<p class="muted">Try fewer words, or a different spelling. Transcripts aren’t perfect: names especially.</p></div>
{/if}

{#if results.length < total}
	<button class="btn" onclick={() => run(true)} disabled={loading}>{loading ? 'Loading…' : 'More results'}</button>
{/if}

<style>
	.searchbox { position: relative; margin: 12px 0; }
	.searchbox :global(svg) { position: absolute; left: 14px; top: 50%; transform: translateY(-50%); color: var(--muted); pointer-events: none; }
	.searchbox input.q { padding: 13px 14px 13px 44px; font-size: 1.05rem; border-radius: 12px; background: var(--card); }
	.filters { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 8px; align-items: center; }
	.filters select { background-color: var(--card); padding-top: 7px; padding-bottom: 7px; font-size: 0.9rem; }
	.fav { padding-left: 4px; }
	.summary { margin: 14px 0 10px; }
	.link, .x { background: none; border: 0; padding: 0; color: var(--accent); cursor: pointer; font: inherit; text-decoration: underline; }
	.x { text-decoration: none; color: var(--muted); font-size: 1rem; margin-left: 4px; }
</style>
