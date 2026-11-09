<script>
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { api, qs } from '#lib/api.js';
	import { session, refreshStats, toastError } from '#lib/session.svelte.js';
	import { onHeard } from '#lib/player.svelte.js';
	import { fmtMonth, fmtAge, plural } from '#lib/format.js';
	import MessageCard from '#lib/components/MessageCard.svelte';
	import Icon from '#lib/components/Icon.svelte';

	const show = $derived(page.url.searchParams.get('show') || 'all');
	let messages = $state([]);
	let nextBefore = $state(null);
	let loading = $state(true);
	let loadingMore = $state(false);

	async function load(more = false) {
		const params = { show, limit: 30, before: more ? nextBefore : undefined };
		more ? (loadingMore = true) : (loading = true);
		try {
			const r = await api('/api/messages' + qs(params));
			messages = more ? [...messages, ...r.messages] : r.messages;
			nextBefore = r.next_before;
		} catch (e) {
			toastError(e);
		} finally {
			loading = loadingMore = false;
		}
	}

	$effect(() => {
		show; // reload when the filter changes
		load();
	});

	// New messages appear without a reload: check when the tab regains focus
	// and every half minute while it's open.
	$effect(() => {
		const check = async () => {
			if (document.hidden || show !== 'all' || !messages.length) return;
			try {
				const r = await api('/api/messages?limit=10');
				const known = new Set(messages.map((m) => m.id));
				const fresh = r.messages.filter((m) => !known.has(m.id));
				if (fresh.length) {
					messages = [...fresh, ...messages];
					refreshStats();
				}
			} catch { /* try again later */ }
		};
		const t = setInterval(check, 30_000);
		document.addEventListener('visibilitychange', check);
		return () => { clearInterval(t); document.removeEventListener('visibilitychange', check); };
	});

	$effect(() => onHeard((id) => {
		const m = messages.find((x) => x.id === id);
		if (m) m.heard = true;
	}));

	const groups = $derived.by(() => {
		const out = [];
		for (const m of messages) {
			const label = fmtMonth(m.recorded_at);
			if (!out.length || out.at(-1).label !== label) out.push({ label, items: [] });
			out.at(-1).items.push(m);
		}
		return out;
	});

	const stats = $derived(session.stats);
	const backupDays = $derived(stats?.backup?.last ? (Date.now() - new Date(stats.backup.last).getTime()) / 86400000 : Infinity);

	async function markAllHeard() {
		try {
			await api('/api/messages/heard-all', { method: 'POST' });
			messages.forEach((m) => (m.heard = true));
			refreshStats();
			if (show === 'unheard') load();
		} catch (e) {
			toastError(e);
		}
	}
</script>

<svelte:head><title>Messages · PhoneHome</title></svelte:head>

<section class="page-head">
	<div>
		<h1>Messages</h1>
		{#if stats}
			<p class="muted">
				{plural(stats.total, 'message')} · {Math.round(stats.seconds / 60)} min
				{#if stats.phone}
					· <a class="phone-link" href="/phone"><span class="dot {stats.phone.online ? 'on' : 'off'}"></span>{stats.phone.online ? 'phone online' : `phone offline since ${fmtAge(stats.phone.age_s)}`}</a>
				{:else}
					· <a href="/phone">phone hasn't checked in yet</a>
				{/if}
			</p>
		{/if}
	</div>
	<nav class="pill-nav" aria-label="Filter">
		<a href="/" class:active={show === 'all'}>All</a>
		<a href="/?show=unheard" class:active={show === 'unheard'}>New{stats?.unheard ? ` (${stats.unheard})` : ''}</a>
		<a href="/?show=favorites" class:active={show === 'favorites'}>Favorites</a>
	</nav>
</section>

{#if stats?.on_this_day && show === 'all'}
	<a class="card memories" href="/memories">
		<Icon name="calendar" size={26} />
		<div><strong>On this day</strong>
			<span class="muted">{plural(stats.on_this_day, 'message')} from this date in years past</span></div>
		<Icon name="chevron" />
	</a>
{/if}

{#if stats?.backup?.integrity_ok === false}
	<a class="card notice bad-notice" href="/backup"><Icon name="x" size={22} />
		<div><strong>Some recordings failed their integrity check</strong>
			<span class="muted">See what was found and how to fix it</span></div><Icon name="chevron" /></a>
{:else if stats?.total && backupDays > 90 && show === 'all'}
	<a class="card notice" href="/backup"><Icon name="download" size={22} />
		<div><strong>{stats.backup.last ? 'Time for a fresh backup' : 'Keep a copy somewhere safe'}</strong>
			<span class="muted">{stats.backup.last ? `The last one was ${Math.round(backupDays)} days ago.` : 'No backup yet. It takes a minute.'}</span></div>
		<Icon name="chevron" /></a>
{/if}

{#if loading}
	<p class="muted" aria-busy="true">Loading…</p>
{:else if !messages.length}
	<section class="card empty">
		{#if show === 'all'}
			<h2>No messages yet</h2>
			<p class="muted">Pick up the phone and leave the first one. It shows up here a few seconds after you hang up.</p>
		{:else if show === 'unheard'}
			<h2>All caught up</h2>
			<p class="muted">You've heard every message.</p>
		{:else}
			<h2>No favorites yet</h2>
			<p class="muted">Tap the star on a message to keep it here.</p>
		{/if}
	</section>
{:else}
	{#each groups as g (g.label)}
		<h2 class="section-label">{g.label}</h2>
		{#each g.items as m (m.id)}
			<MessageCard {m} />
		{/each}
	{/each}
{/if}

<div class="foot">
	{#if nextBefore}
		<button class="btn" onclick={() => load(true)} disabled={loadingMore}>{loadingMore ? 'Loading…' : 'Older messages'}</button>
	{/if}
	{#if stats?.unheard && show !== 'favorites'}
		<button class="btn ghost" onclick={markAllHeard}>Mark all as heard</button>
	{/if}
	<span class="spacer"></span>
	<a class="muted small" href="/trash">Trash{#if stats?.trash} ({stats.trash}){/if}</a>
</div>

<style>
	.phone-link { color: inherit; text-decoration: none; }
	.phone-link:hover { text-decoration: underline; }
	.memories {
		display: flex; align-items: center; gap: 14px; text-decoration: none; color: var(--fg);
		background: var(--accent-soft); border-color: transparent;
	}
	.memories :global(svg:first-child) { color: var(--accent); flex: none; }
	.memories div { flex: 1; display: flex; flex-direction: column; }
	.notice { display: flex; align-items: center; gap: 14px; text-decoration: none; color: var(--fg); }
	.notice :global(svg:first-child) { color: var(--accent); flex: none; }
	.notice div { flex: 1; display: flex; flex-direction: column; }
	.bad-notice { border-color: color-mix(in srgb, var(--bad) 45%, var(--line)); }
	.bad-notice :global(svg:first-child) { color: var(--bad); }
	.foot { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-top: 20px; }
</style>
