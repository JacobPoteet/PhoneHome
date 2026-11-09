<script>
	import { api } from '#lib/api.js';
	import { toast, toastError } from '#lib/session.svelte.js';
	import { fmtAge, fmtSpan, fmtBytes } from '#lib/format.js';
	import Icon from '#lib/components/Icon.svelte';

	let data = $state(null);
	let logs = $state('');
	let busy = $state('');

	async function load() {
		try {
			data = await api('/api/phone');
		} catch (e) {
			toastError(e);
		}
	}

	$effect(() => {
		load();
		const t = setInterval(load, 10_000);
		return () => clearInterval(t);
	});

	const phone = $derived(data?.phones?.[0]);
	const s = $derived(phone?.status || {});
	const c = $derived(s.controller || {});

	async function act(action, body = {}, done = 'Done.') {
		if (action === 'reboot' && !confirm('Reboot the phone’s computer? It takes about a minute.')) return;
		if (action === 'restart' && !confirm('Restart the phone software?')) return;
		busy = action + (body.mode || body.route || '');
		try {
			await api(`/api/phone/${action}`, { method: 'POST', body });
			toast(done);
			setTimeout(load, 1500);
		} catch (e) {
			toastError(e);
		} finally {
			busy = '';
		}
	}

	async function loadLogs(e) {
		if (!e.currentTarget.open || logs) return;
		try {
			logs = (await api('/api/phone/logs?lines=300')).log || '(no log)';
		} catch (err) {
			logs = err.message;
		}
	}

	const label = (v, unit = '') => (v === undefined || v === null ? '—' : `${v}${unit}`);
</script>

<svelte:head><title>Phone · PhoneHome</title></svelte:head>

<h1>Phone</h1>

{#if !data}
	<p class="muted">Loading…</p>
{:else if !phone}
	<section class="card empty">
		<Icon name="phone" size={34} />
		<h2>The phone hasn’t checked in yet</h2>
		<p class="muted">When the phone’s software runs with <code>server.url</code> pointing here and the same token as this
			server’s <code>PHONEHOME_AGENT_TOKEN</code>, it checks in every minute and shows up here.</p>
	</section>
{:else}
	<section class="card">
		<div class="status-line">
			<span class="dot {phone.online ? 'on' : 'off'}"></span>
			<strong>{phone.device}</strong>
			<span class="muted">{phone.online ? 'online' : 'offline'} · last check-in {fmtAge(phone.age_s)}</span>
		</div>
		<dl class="grid">
			<div><dt>Right now</dt><dd>{(c.state || '—').replaceAll('_', ' ')}{c.off_hook ? ' (handset up)' : ''}</dd></div>
			<div><dt>Audio</dt><dd>{#if s.audio?.healthy}OK{:else if s.audio}<span class="bad">Problem: {s.audio.last_error || 'no sound card'}</span>{:else}—{/if}</dd></div>
			<div><dt>Waiting to upload</dt><dd>{label(s.store?.outbox)}{s.store?.incoming ? ` (+${s.store.incoming} recording)` : ''}</dd></div>
			<div><dt>Too short or silent (discarded)</dt><dd>{label(s.store?.discarded)}</dd></div>
			<div><dt>Free space on the phone</dt><dd>{s.store?.free_mb != null ? fmtBytes(s.store.free_mb * 1048576) : '—'}</dd></div>
			<div><dt>CPU temperature</dt><dd>{label(s.system?.cpu_temp_c, ' °C')}</dd></div>
			<div><dt>Software</dt><dd>{label(s.version)}{s.simulate ? ' (simulated)' : ''}</dd></div>
			<div><dt>Running for</dt><dd>{s.uptime_s != null ? fmtSpan(s.uptime_s) : '—'}</dd></div>
			<div><dt>Clock synced</dt><dd>{s.clock_synced == null ? '—' : s.clock_synced ? 'yes' : 'no'}</dd></div>
			<div><dt>Ringer switch</dt><dd>{label(c.ringer_switch)}</dd></div>
			<div><dt>Settings version</dt><dd><code>{label(s.sync?.config_version)}</code></dd></div>
			<div><dt>Last upload problem</dt><dd>{s.sync?.last_upload_error || 'none'}</dd></div>
		</dl>
	</section>

	{#if data.control}
		<section class="card">
			<h2>Try it</h2>
			<div class="actions">
				<button class="btn primary" disabled={!!busy} onclick={() => act('ring', { mode: 'auto' }, 'Ringing the phone.')}><Icon name="bell" size={18} /> Ring now</button>
				<button class="btn" disabled={!!busy} onclick={() => act('ring', { mode: 'memory' }, 'Ringing: answering plays a memory.')}>Ring → memory</button>
				<button class="btn" disabled={!!busy} onclick={() => act('ring', { mode: 'prompt' }, 'Ringing: answering asks a prompt.')}>Ring → prompt</button>
				<button class="btn" disabled={!!busy} onclick={() => act('tone', { route: 'earpiece' }, 'Test tone in the earpiece.')}>Tone: earpiece</button>
				<button class="btn" disabled={!!busy} onclick={() => act('tone', { route: 'ringer' }, 'Test tone on the ringer.')}>Tone: ringer</button>
				<button class="btn" disabled={!!busy} onclick={() => act('sync', {}, 'The phone is fetching its settings.')}><Icon name="refresh" size={16} /> Sync now</button>
			</div>
			<div class="actions secondary">
				<button class="btn small" disabled={!!busy} onclick={() => act('restart', {}, 'Restarting the phone software.')}>Restart software</button>
				<button class="btn small danger" disabled={!!busy} onclick={() => act('reboot', {}, 'Rebooting the phone.')}>Reboot phone</button>
			</div>
		</section>
	{:else}
		<p class="muted small">Set <code>PHONEHOME_AGENT_CONTROL_URL</code> and <code>PHONEHOME_AGENT_CONTROL_TOKEN</code> on the
			server to get Ring now, test tones and restart buttons here.</p>
	{/if}

	{#if c.history?.length}
		<section class="card">
			<h2>Recent activity</h2>
			<ul class="list-plain log">
				{#each c.history.slice(0, 15) as e}
					<li><span class="muted">{e.at.slice(5, 16).replace('T', ' ')}</span>
						{e.event.replaceAll('_', ' ')}{#if e.id}{' '}<a href="/m/{e.id}"><code>{e.id}</code></a>{/if}{e.seconds ? ` (${e.seconds} s)` : ''}{e.reason && e.reason !== 'ok' ? ` · ${e.reason.replaceAll('_', ' ')}` : ''}</li>
				{/each}
			</ul>
		</section>
	{/if}

	{#if data.control && phone.online}
		<details class="card" ontoggle={loadLogs}><summary>Phone log</summary><pre class="logs">{logs || 'Loading…'}</pre></details>
	{/if}
	<details class="card"><summary>Everything the phone reports</summary><pre class="logs">{JSON.stringify(s, null, 2)}</pre></details>
{/if}

<style>
	.status-line { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 14px; }
	.empty :global(svg) { color: var(--muted); }
	.actions { display: flex; flex-wrap: wrap; gap: 8px; }
	.secondary { margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--line); }
	.log li { padding: 4px 0; border-bottom: 1px solid var(--line); font-size: 0.9rem; }
	.log li:last-child { border-bottom: 0; }
</style>
