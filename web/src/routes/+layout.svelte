<script>
	import '../app.css';
	import { page } from '$app/state';
	import { goto, afterNavigate } from '$app/navigation';
	import { session, checkSession, refreshStats } from '#lib/session.svelte.js';
	import Icon from '#lib/components/Icon.svelte';
	import Toasts from '#lib/components/Toasts.svelte';

	let { children } = $props();
	let error = $state('');

	const nav = [
		{ href: '/', label: 'Messages', icon: 'messages', match: (p) => p === '/' || p.startsWith('/m/') || p === '/trash' },
		{ href: '/search', label: 'Search', icon: 'search', match: (p) => p.startsWith('/search') },
		{ href: '/memories', label: 'Memories', icon: 'calendar', match: (p) => p.startsWith('/memories') },
		{ href: '/greetings', label: 'Greetings', icon: 'greeting', match: (p) => p.startsWith('/greetings') },
		{ href: '/phone', label: 'Phone', icon: 'phone', match: (p) => p.startsWith('/phone') }
	];

	const isLogin = $derived(page.url.pathname === '/login');

	$effect(() => {
		checkSession()
			.then((me) => {
				if (!me.user && !isLogin) {
					goto('/login?next=' + encodeURIComponent(page.url.pathname + page.url.search), { replaceState: true });
				} else if (me.user) {
					refreshStats();
				}
			})
			.catch((e) => (error = e.message));
		const t = setInterval(refreshStats, 60_000);
		return () => clearInterval(t);
	});

	afterNavigate(() => window.scrollTo?.({ top: 0 }));

	const unheard = $derived(session.stats?.unheard || 0);
	const memories = $derived(session.stats?.on_this_day || 0);
</script>

{#if error}
	<main class="wrap"><div class="card empty"><h2>Can't reach the server</h2><p class="muted">{error}</p>
		<button class="btn" onclick={() => location.reload()}>Try again</button></div></main>
{:else if !session.checked}
	<div class="boot" aria-busy="true"></div>
{:else if isLogin}
	{@render children()}
{:else if !session.user}
	<div class="boot" aria-busy="true"></div>
{:else}
	<header class="topbar">
		<a class="brand" href="/"><img src="/icon.svg" alt="" width="26" height="26" /> PhoneHome</a>
		<nav class="desktop" aria-label="Main">
			{#each nav as item}
				<a href={item.href} class:active={item.match(page.url.pathname)} aria-current={item.match(page.url.pathname) ? 'page' : undefined}>
					{item.label}
					{#if item.icon === 'messages' && unheard}<span class="badge">{unheard}</span>{/if}
					{#if item.icon === 'calendar' && memories}<span class="badge soft">{memories}</span>{/if}
				</a>
			{/each}
		</nav>
		<a class="gear" href="/settings" class:active={page.url.pathname === '/settings'} aria-label="Settings">
			<Icon name="settings" size={22} />
		</a>
	</header>
	<main class="wrap">
		{@render children()}
	</main>
	<nav class="tabbar" aria-label="Main">
		{#each nav as item}
			<a href={item.href} class:active={item.match(page.url.pathname)} aria-current={item.match(page.url.pathname) ? 'page' : undefined}>
				<span class="ico">
					<Icon name={item.icon} size={22} />
					{#if item.icon === 'messages' && unheard}<span class="badge">{unheard}</span>{/if}
					{#if item.icon === 'calendar' && memories}<span class="badge soft">{memories}</span>{/if}
				</span>
				<span>{item.label}</span>
			</a>
		{/each}
	</nav>
{/if}
<Toasts />

<style>
	.boot { min-height: 100vh; }
	.topbar {
		position: sticky; top: 0; z-index: 20; display: flex; align-items: center; gap: 16px;
		padding: 10px 16px; padding-top: max(10px, env(safe-area-inset-top));
		background: color-mix(in srgb, var(--card) 92%, transparent); backdrop-filter: blur(8px);
		border-bottom: 1px solid var(--line);
	}
	.brand { display: flex; align-items: center; gap: 8px; font-weight: 700; color: var(--fg); text-decoration: none; }
	.brand img { border-radius: 7px; }
	.desktop { display: flex; gap: 2px; margin-left: auto; }
	.desktop a, .gear {
		color: var(--muted); text-decoration: none; padding: 6px 11px; border-radius: 999px; font-size: 0.95rem;
		display: inline-flex; align-items: center; gap: 6px;
	}
	.desktop a:hover, .gear:hover { color: var(--fg); }
	.desktop a.active, .gear.active { color: var(--fg); background: var(--chip); }
	.gear { padding: 6px; }
	.badge.soft { background: var(--chip); color: var(--fg); border: 1px solid var(--line); }
	:global(.wrap) { max-width: 820px; margin: 0 auto; padding: 20px 16px 48px; }
	.tabbar { display: none; }

	@media (max-width: 760px) {
		:global(:root) { --nav-h: calc(60px + env(safe-area-inset-bottom)); }
		.desktop { display: none; }
		.gear { margin-left: auto; }
		:global(.wrap) { padding-bottom: calc(var(--nav-h) + 32px); }
		.tabbar {
			display: flex; position: fixed; bottom: 0; left: 0; right: 0; z-index: 20; height: var(--nav-h);
			padding-bottom: env(safe-area-inset-bottom); background: color-mix(in srgb, var(--card) 95%, transparent);
			backdrop-filter: blur(8px); border-top: 1px solid var(--line);
		}
		.tabbar a {
			flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 2px;
			color: var(--muted); text-decoration: none; font-size: 0.7rem; font-weight: 500;
		}
		.tabbar a.active { color: var(--accent); }
		.ico { position: relative; display: flex; }
		.ico .badge { position: absolute; top: -6px; left: 14px; font-size: 0.65rem; }
	}
</style>
