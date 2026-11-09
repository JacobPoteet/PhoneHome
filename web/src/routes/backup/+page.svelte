<script>
	import { api, qs } from '#lib/api.js';
	import { toast, toastError, refreshStats } from '#lib/session.svelte.js';
	import { fmtAge, fmtBytes, fmtShortDate, plural } from '#lib/format.js';
	import Icon from '#lib/components/Icon.svelte';

	let b = $state(null);
	let mp3 = $state(true);
	let trash = $state(false);
	let copied = $state(false);

	async function load() {
		try {
			b = await api('/api/backup');
		} catch (e) {
			toastError(e);
		}
	}

	$effect(() => {
		load();
	});

	// While a check runs (or right after starting a download), refresh now and then.
	let watchUntil = $state(0);
	$effect(() => {
		if (!b?.verifying && Date.now() > watchUntil) return;
		const t = setInterval(() => {
			load();
			refreshStats();
		}, 4000);
		return () => clearInterval(t);
	});

	const ago = (iso) => (iso ? fmtAge((Date.now() - new Date(iso).getTime()) / 1000) : 'never');
	const daysSince = $derived(b?.last_any ? (Date.now() - new Date(b.last_any).getTime()) / 86400000 : Infinity);
	const exportSize = $derived(b ? b.archive.bytes - (mp3 ? 0 : b.archive.by_kind['playback copies']) -
		(trash ? 0 : b.archive.by_kind.trash) : 0);
	const integrity = $derived(b?.integrity);
	const command = $derived(
		'powershell -ExecutionPolicy Bypass -File .\\backup.ps1 -Source \\\\nas\\phonehome -Destination E:\\PhoneHome-Backup' +
			(b?.backup_token ? ` -Server ${location.origin} -Token YOUR_BACKUP_TOKEN` : ''));

	async function verify() {
		try {
			await api('/api/backup/verify', { method: 'POST' });
			toast('Checking every recording. This takes a minute or two.');
			load();
		} catch (e) {
			toastError(e);
		}
	}

	function startedExport() {
		toast('Your download is starting. A big archive takes a while.', { timeout: 6000 });
		watchUntil = Date.now() + 5 * 60_000;
		setTimeout(load, 4000);
	}

	async function copyCommand() {
		try {
			await navigator.clipboard.writeText(command);
			copied = true;
			setTimeout(() => (copied = false), 2000);
		} catch {
			toast('Select the command and copy it by hand.', { kind: 'err' });
		}
	}

	const KIND_COLORS = {
		recordings: 'var(--accent)',
		'playback copies': 'color-mix(in srgb, var(--accent) 55%, var(--card))',
		'transcripts and details': 'var(--ok)',
		'greetings and prompts': 'var(--star)',
		trash: 'var(--muted)',
		other: 'var(--line)'
	};
</script>

<svelte:head><title>Backup · PhoneHome</title></svelte:head>

<a class="back muted" href="/settings"><Icon name="back" size={18} /> Settings</a>
<h1>Backup</h1>
<p class="muted lead">The archive is ordinary files on your NAS. Keep a second copy somewhere else, and PhoneHome checks
	that nothing has been damaged.</p>

{#if !b}
	<p class="muted">Loading…</p>
{:else}
	<section class="status">
		<div class="card tile" class:warn={daysSince > 90}>
			<Icon name="download" size={24} />
			<div>
				<strong>{b.last_any ? `Last backup ${ago(b.last_any)}` : 'Never backed up'}</strong>
				<span class="muted small">
					{#if !b.last_any}Download a copy below, or set up the backup script.
					{:else if daysSince > 90}It's been a while: time for a fresh copy.
					{:else}{b.last_backup && b.last_backup.at === b.last_any ? `Script backup to ${b.last_backup.destination || 'a drive'}` : 'Downloaded from this page'}{/if}
				</span>
			</div>
		</div>
		<div class="card tile" class:warn={integrity && !integrity.ok}>
			<Icon name={integrity && !integrity.ok ? 'x' : 'check'} size={24} />
			<div>
				{#if b.verifying}
					<strong>Checking recordings…</strong><span class="muted small">Re-reading every file.</span>
				{:else if !integrity}
					<strong>Not checked yet</strong><span class="muted small">Runs automatically every {b.integrity_check_days} days.</span>
				{:else if integrity.ok}
					<strong>All {plural(integrity.files, 'recording')} check out</strong>
					<span class="muted small">Checked {ago(integrity.checked_at)} against SHA256SUMS</span>
				{:else}
					<strong class="bad">Some recordings don't match</strong>
					<span class="muted small">Checked {ago(integrity.checked_at)}. Details below.</span>
				{/if}
			</div>
			<button class="btn small" onclick={verify} disabled={b.verifying}>Check now</button>
		</div>
	</section>

	{#if integrity && (integrity.changed.length || integrity.missing.length || integrity.untracked.length)}
		<section class="card">
			<h2>What the last check found</h2>
			{#if integrity.changed.length}
				<p class="bad"><strong>Changed:</strong> these files no longer match the checksum they had when they were saved.
					That usually means disk damage. Restore them from a backup (your backup copy is checked the same way).</p>
				<ul class="files">{#each integrity.changed as f}<li><code>{f}</code></li>{/each}</ul>
			{/if}
			{#if integrity.missing.length}
				<p class="bad"><strong>Missing:</strong> listed in SHA256SUMS but not on disk.</p>
				<ul class="files">{#each integrity.missing as f}<li><code>{f}</code></li>{/each}</ul>
			{/if}
			{#if integrity.untracked.length}
				<p><strong>Not listed:</strong> recordings that aren't in SHA256SUMS (for example, copied in by hand).
					They're fine, just not protected by a checksum yet.</p>
				<ul class="files">{#each integrity.untracked as f}<li><code>{f}</code></li>{/each}</ul>
			{/if}
		</section>
	{/if}

	<section class="card stack">
		<h2>Download everything</h2>
		<p>One zip file with every recording, transcript and detail, plus an <strong>offline viewer</strong>: open
			<code>index.html</code> from the unzipped folder to browse and play everything in any browser, with no
			server and no internet. Good for a USB stick in a drawer, or a relative’s computer.</p>
		<label class="check"><input type="checkbox" bind:checked={mp3} /> Include the MP3 listening copies
			<span class="muted small">(the originals are always included)</span></label>
		<label class="check"><input type="checkbox" bind:checked={trash} /> Include the trash</label>
		<div class="row">
			<a class="btn primary" href="/api/export{qs({ mp3: mp3 ? 'true' : 'false', trash: trash ? 'true' : '' })}"
				download onclick={startedExport}><Icon name="download" size={18} /> Download zip</a>
			<span class="muted small">about {fmtBytes(exportSize)}</span>
		</div>
		{#if b.last_export}
			<p class="muted small">Last download: {fmtShortDate(b.last_export.at)} ({plural(b.last_export.files, 'file')},
				{fmtBytes(b.last_export.bytes)})</p>
		{/if}
	</section>

	<section class="card stack">
		<h2>Copy to a drive automatically (Windows)</h2>
		<p><code>tools/backup.ps1</code> in the PhoneHome repository copies new files from the NAS to a folder you choose
			(an external drive), never deletes anything, and checks every recording afterwards. Later runs only copy
			what’s new.</p>
		<div class="cmd">
			<code>{command}</code>
			<button class="btn small" onclick={copyCommand}>{copied ? 'Copied' : 'Copy'}</button>
		</div>
		<p class="hint">Change <code>\\nas\phonehome</code> to your NAS share and <code>E:\PhoneHome-Backup</code> to your drive.
			{#if !b.backup_token}Set <code>PHONEHOME_BACKUP_TOKEN</code> on the server to have the script report back here.{/if}
			See <code>tools/README.md</code> to run it every week.</p>
		{#if b.last_backup}
			<div class="report">
				<strong>Last script backup:</strong> {ago(b.last_backup.at)}{b.last_backup.computer ? ` from ${b.last_backup.computer}` : ''}
				to <code>{b.last_backup.destination}</code>.
				{plural(b.last_backup.copied || 0, 'new file')}, {b.last_backup.updated || 0} updated,
				{b.last_backup.verified || 0} recordings checked.
				{#if b.last_backup.problems?.length}
					<p class="bad small">{plural(b.last_backup.problems.length, 'problem')}:</p>
					<ul class="files">{#each b.last_backup.problems as p}<li>{p}</li>{/each}</ul>
				{/if}
			</div>
		{/if}
	</section>

	<section class="card">
		<h2>What’s in the archive</h2>
		<p>{plural(b.archive.files, 'file')}, {fmtBytes(b.archive.bytes)} in <code>{b.archive_dir}</code></p>
		<div class="bar" aria-hidden="true">
			{#each Object.entries(b.archive.by_kind) as [kind, bytes]}
				{#if bytes}<span style:width="{(100 * bytes) / b.archive.bytes}%" style:background={KIND_COLORS[kind]}></span>{/if}
			{/each}
		</div>
		<ul class="legend">
			{#each Object.entries(b.archive.by_kind) as [kind, bytes]}
				{#if bytes}<li><span class="sw" style:background={KIND_COLORS[kind]}></span>{kind} <span class="muted">{fmtBytes(bytes)}</span></li>{/if}
			{/each}
		</ul>
		<p class="muted small">The archive also has its own <code>index.html</code> offline viewer{b.viewer_updated
			? `, last updated ${ago(b.viewer_updated)}` : ''}, so any copy of the folder can be browsed without PhoneHome.</p>
	</section>
{/if}

<style>
	.back { display: inline-flex; align-items: center; gap: 4px; text-decoration: none; margin-bottom: 12px; }
	.lead { margin: 0 0 16px; }
	.status { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 14px; }
	.status .card { margin: 0; }
	.tile { display: flex; gap: 12px; align-items: flex-start; }
	.tile > :global(svg) { color: var(--ok); flex: none; margin-top: 2px; }
	.tile div { flex: 1; display: flex; flex-direction: column; }
	.tile.warn { border-color: color-mix(in srgb, var(--bad) 45%, var(--line)); }
	.tile.warn > :global(svg) { color: var(--bad); }
	.stack p { margin: 0; }
	.files { margin: 6px 0 12px; padding-left: 18px; font-size: 0.85rem; max-height: 200px; overflow: auto; }
	.cmd { display: flex; gap: 8px; align-items: flex-start; background: var(--bg); border: 1px solid var(--line);
		border-radius: 10px; padding: 10px; }
	.cmd code { flex: 1; overflow-wrap: anywhere; font-size: 0.82rem; }
	.report { background: var(--bg); border-radius: 10px; padding: 10px 12px; font-size: 0.9rem; }
	.bar { display: flex; height: 12px; border-radius: 6px; overflow: hidden; background: var(--chip); margin: 10px 0; }
	.legend { list-style: none; padding: 0; margin: 0 0 10px; display: flex; flex-wrap: wrap; gap: 6px 16px; font-size: 0.88rem; }
	.sw { display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 6px; }
	@media (max-width: 640px) { .status { grid-template-columns: 1fr; } }
</style>
