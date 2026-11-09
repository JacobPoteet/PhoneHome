<script>
	import { goto } from '$app/navigation';
	import { api } from '#lib/api.js';
	import { session, toast, toastError } from '#lib/session.svelte.js';
	import Icon from '#lib/components/Icon.svelte';

	let sys = $state(null);
	let behavior = $state(null);
	let version = $state('');
	let json = $state('');
	let jsonError = $state('');
	let saving = $state(false);

	// The settings people change most, as plain fields. Everything else stays
	// editable in the JSON below (it's the same document).
	const FIELDS = [
		{ path: ['recording', 'max_duration_s'], label: 'Longest message', unit: 'minutes', scale: 60, def: 180, min: 0.5, max: 30, step: 0.5 },
		{ path: ['recording', 'warning_before_end_s'], label: 'Warning before the end', unit: 'seconds', scale: 1, def: 30, min: 0, max: 120, step: 5 },
		{ path: ['recording', 'min_duration_s'], label: 'Shortest message kept', unit: 'seconds', scale: 1, def: 2, min: 0, max: 30, step: 0.5 }
	];

	function get(obj, path) { return path.reduce((o, k) => (o == null ? undefined : o[k]), obj); }
	function set(obj, path, value) {
		let o = obj;
		for (const k of path.slice(0, -1)) o = o[k] ??= {};
		o[path.at(-1)] = value;
	}

	async function load() {
		try {
			const [s, b] = await Promise.all([api('/api/system'), api('/api/settings/behavior')]);
			sys = s;
			behavior = b.settings;
			version = b.version;
			json = JSON.stringify(b.settings, null, 2);
		} catch (e) {
			toastError(e);
		}
	}
	$effect(() => { load(); });

	function fieldValue(f) {
		const v = get(behavior, f.path);
		return (v ?? f.def) / f.scale;
	}

	function setField(f, raw) {
		const n = Number(raw);
		if (!Number.isFinite(n)) return;
		set(behavior, f.path, Math.round(n * f.scale * 100) / 100);
		json = JSON.stringify(behavior, null, 2);
	}

	function jsonInput() {
		try {
			const parsed = JSON.parse(json || '{}');
			if (typeof parsed !== 'object' || Array.isArray(parsed) || parsed === null) throw new Error('must be a JSON object');
			behavior = parsed;
			jsonError = '';
		} catch (e) {
			jsonError = e.message;
		}
	}

	async function save() {
		if (jsonError) return;
		saving = true;
		try {
			const r = await api('/api/settings/behavior', { method: 'PUT', body: { settings: behavior } });
			version = r.version;
			behavior = r.settings;
			json = JSON.stringify(r.settings, null, 2);
			toast('Saved. The phone picks it up within a minute.');
		} catch (e) {
			toastError(e);
		} finally {
			saving = false;
		}
	}

	async function transcribeMissing() {
		try {
			const r = await api('/api/system/transcribe-missing', { method: 'POST' });
			toast(r.queued ? `Transcribing ${r.queued} message(s) in the background.` : 'Every message already has a transcript.');
			load();
		} catch (e) {
			toastError(e);
		}
	}

	async function logout() {
		await api('/api/auth/logout', { method: 'POST' }).catch(() => {});
		session.user = null;
		goto('/login');
	}
</script>

<svelte:head><title>Settings · PhoneHome</title></svelte:head>

<h1>Settings</h1>

{#if !sys}
	<p class="muted">Loading…</p>
{:else}
	<a class="card backup-link" href="/backup">
		<Icon name="download" size={24} />
		<div><strong>Backup</strong>
			<span class="muted small">Download everything, copy to a drive, and check that no recording is damaged</span></div>
		<Icon name="chevron" />
	</a>

	<section class="card stack">
		<h2>Recording</h2>
		<div class="fields">
			{#each FIELDS as f}
				<label class="field"><span>{f.label}</span>
					<div class="unit"><input type="number" value={fieldValue(f)} min={f.min} max={f.max} step={f.step}
						oninput={(e) => setField(f, e.currentTarget.value)} /><span>{f.unit}</span></div>
				</label>
			{/each}
		</div>
		<details>
			<summary class="small">All phone settings (JSON)</summary>
			<p class="hint">Ringer, sounds and recording thresholds, in the format the phone reads (see the agent’s
				<code>behavior.py</code>). Greetings and prompts are managed on their own page. Version <code>{version}</code>.</p>
			<textarea class="code" rows="14" bind:value={json} oninput={jsonInput} spellcheck="false" aria-label="Phone settings JSON"></textarea>
			{#if jsonError}<p class="bad small">Not valid JSON yet: {jsonError}</p>{/if}
		</details>
		<div class="row"><button class="btn primary" onclick={save} disabled={saving || !!jsonError}>{saving ? 'Saving…' : 'Save'}</button></div>
	</section>

	<section class="card">
		<h2>Transcription</h2>
		{#if sys.transcription.enabled}
			<p>On, using <strong>{sys.transcription.model}</strong> (English, on this server’s CPU).
				New messages are transcribed a minute or two after they arrive.</p>
			<button class="btn small" onclick={transcribeMissing}>Transcribe messages that don’t have one</button>
		{:else if !sys.transcription.installed}
			<p class="muted">Off: faster-whisper isn’t installed on the server. The Docker image includes it; for a manual
				install use <code>pip install "phonehome-server[transcribe]"</code>.</p>
		{:else}
			<p class="muted">Off (<code>PHONEHOME_TRANSCRIBE=false</code>).</p>
		{/if}
	</section>

	<section class="card">
		<h2>Server</h2>
		<dl class="kv">
			<dt>Version</dt><dd>{sys.version}</dd>
			<dt>Archive folder</dt><dd><code>{sys.archive_dir}</code></dd>
			<dt>ffmpeg</dt><dd>{sys.ffmpeg ? 'found' : 'missing: no MP3 copies, and greetings can’t be converted'}</dd>
			<dt>Background jobs</dt><dd>{Object.entries(sys.jobs.counts).map(([k, v]) => `${k} ${v}`).join(', ') || 'none'}</dd>
			<dt>Trash</dt><dd>deleted messages are kept {sys.trash_days} days</dd>
		</dl>
		{#if sys.jobs.recent_failures.length}
			<p class="bad small">Recent failures:</p>
			<ul class="small">{#each sys.jobs.recent_failures as j}<li>{j.kind} <code>{j.message_id}</code>: {j.last_error}</li>{/each}</ul>
		{/if}
	</section>

	<section class="card">
		<h2>Accounts</h2>
		<p>{sys.users.join(', ')}</p>
		<p class="muted small">Add an account or change a password on the server:
			<code>docker compose exec phonehome phonehome-server adduser NAME</code></p>
		<button class="btn small" onclick={logout}>Sign out {session.user}</button>
	</section>
{/if}

<style>
	.backup-link { display: flex; align-items: center; gap: 14px; text-decoration: none; color: var(--fg); }
	.backup-link :global(svg:first-child) { color: var(--accent); flex: none; }
	.backup-link div { flex: 1; display: flex; flex-direction: column; }
	.fields { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; }
	.unit { display: flex; align-items: center; gap: 8px; }
	.unit input { max-width: 110px; }
	.unit span { color: var(--muted); font-size: 0.9rem; }
	details summary { cursor: pointer; color: var(--muted); }
	details textarea { margin-top: 8px; }
	.hint { margin-top: 6px; }
</style>
