<script>
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { api } from '#lib/api.js';
	import { session, refreshStats } from '#lib/session.svelte.js';

	let username = $state('');
	let password = $state('');
	let error = $state('');
	let busy = $state(false);
	const first = $derived(session.firstRun);

	function next() {
		const n = page.url.searchParams.get('next') || '/';
		return n.startsWith('/') && !n.startsWith('//') && n !== '/login' ? n : '/';
	}

	async function submit(e) {
		e.preventDefault();
		busy = true;
		error = '';
		try {
			const r = await api(first ? '/api/auth/setup' : '/api/auth/login', { method: 'POST', body: { username, password } });
			session.user = r.user;
			session.firstRun = false;
			refreshStats();
			goto(next(), { replaceState: true });
		} catch (err) {
			error = err.message;
		} finally {
			busy = false;
		}
	}

	$effect(() => {
		if (session.user) goto(next(), { replaceState: true });
	});
</script>

<svelte:head><title>Sign in · PhoneHome</title></svelte:head>

<main class="login">
	<form class="card stack" onsubmit={submit}>
		<img src="/icon.svg" alt="" width="52" height="52" />
		<h1>PhoneHome</h1>
		{#if first}
			<p class="muted">Welcome! Create the first account. You can add another later with
				<code>phonehome-server adduser</code>.</p>
		{/if}
		<label class="field"><span>Username</span>
			<input bind:value={username} autocomplete="username" autocapitalize="none" required />
		</label>
		<label class="field"><span>Password</span>
			<input bind:value={password} type="password" autocomplete={first ? 'new-password' : 'current-password'}
				minlength={first ? 8 : undefined} required />
			{#if first}<span class="hint">At least 8 characters.</span>{/if}
		</label>
		{#if error}<p class="bad small" role="alert">{error}</p>{/if}
		<button class="btn primary" disabled={busy}>{first ? 'Create account' : 'Sign in'}</button>
	</form>
</main>

<style>
	.login { min-height: 100vh; display: grid; place-items: center; padding: 16px; }
	form { width: min(380px, 100%); text-align: left; }
	img { border-radius: 12px; align-self: center; }
	h1 { text-align: center; margin-bottom: 4px; }
	p { margin: 0; }
</style>
