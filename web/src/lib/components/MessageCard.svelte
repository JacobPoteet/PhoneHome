<script>
	import { player, toggle, seek } from '#lib/player.svelte.js';
	import { api } from '#lib/api.js';
	import { toastError } from '#lib/session.svelte.js';
	import { fmtTime, fmtDuration, fmtShortDate, KIND_LABELS, highlight, messageTitle } from '#lib/format.js';
	import Icon from './Icon.svelte';

	let { m } = $props();

	const active = $derived(player.id === m.id);
	const progress = $derived(active && player.duration ? Math.min(1, player.time / player.duration) : 0);

	async function favorite() {
		const was = m.favorite;
		m.favorite = !was;
		try {
			await api(`/api/messages/${m.id}`, { method: 'PATCH', body: { favorite: !was } });
		} catch (e) {
			m.favorite = was;
			toastError(e);
		}
	}

	function scrub(e) {
		const r = e.currentTarget.getBoundingClientRect();
		seek(m, Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)));
	}

	function scrubKey(e) {
		if (!active || !player.duration) return;
		const step = e.key === 'ArrowRight' ? 5 : e.key === 'ArrowLeft' ? -5 : 0;
		if (step) {
			e.preventDefault();
			seek(m, Math.max(0, Math.min(1, (player.time + step) / player.duration)));
		}
	}
</script>

<article class="card msg" class:unheard={!m.heard}>
	<button class="play" class:playing={active && player.playing} onclick={() => toggle(m)}
		aria-label={active && player.playing ? 'Pause' : `Play ${messageTitle(m)}`}>
		<Icon name={active && player.playing ? 'pause' : 'play'} size={22} />
	</button>
	<div class="body">
		<div class="head">
			<a class="title" href="/m/{m.id}">
				{#if !m.heard}<span class="new" title="New"></span>{/if}
				{messageTitle(m)}
			</a>
			<button class="star" class:on={m.favorite} onclick={favorite} aria-pressed={m.favorite}
				aria-label={m.favorite ? 'Remove from favorites' : 'Add to favorites'}><Icon name="star" size={20} /></button>
		</div>
		<p class="meta">
			{m.title ? fmtShortDate(m.recorded_at) + ' · ' : ''}{fmtTime(m.recorded_at)} · {fmtDuration(m.duration_s)}
			{#if m.kind !== 'guest'} · <span class="kind">{m.prompt_title ? `“${m.prompt_title}”` : KIND_LABELS[m.kind] || m.kind}</span>{/if}
		</p>
		<div class="bar" class:active role="slider" tabindex={active ? 0 : -1} aria-label="Position"
			aria-valuemin="0" aria-valuemax={Math.round(player.duration || m.duration_s || 0)}
			aria-valuenow={active ? Math.round(player.time) : 0} onclick={scrub} onkeydown={scrubKey}>
			<div class="fill" style:width="{progress * 100}%"></div>
		</div>
		{#if m.snippet}
			<p class="excerpt">{@html highlight(m.snippet)}</p>
		{:else if m.excerpt}
			<p class="excerpt">{m.excerpt}</p>
		{:else if m.transcript_state === 'pending'}
			<p class="excerpt muted small">Transcribing…</p>
		{/if}
		{#if m.people.length || m.tags.length}
			<div class="chips">
				{#each m.people as p}<a class="chip" href="/search?person={encodeURIComponent(p)}">{p}</a>{/each}
				{#each m.tags as t}<a class="chip" href="/search?tag={encodeURIComponent(t)}">#{t}</a>{/each}
			</div>
		{/if}
	</div>
</article>

<style>
	.msg { display: flex; gap: 14px; align-items: flex-start; padding: 14px 16px; }
	.msg.unheard { border-color: color-mix(in srgb, var(--accent) 55%, var(--line)); }
	.play {
		flex: none; width: 46px; height: 46px; border-radius: 50%; border: 0; cursor: pointer; display: grid; place-items: center;
		background: var(--accent-soft); color: var(--accent); margin-top: 2px;
	}
	.play.playing, .play:hover { background: var(--accent); color: var(--accent-fg); }
	.body { flex: 1; min-width: 0; }
	.head { display: flex; align-items: flex-start; gap: 8px; }
	.title { flex: 1; font-weight: 600; color: var(--fg); text-decoration: none; font-size: 1.03rem; line-height: 1.35; }
	.title:hover { text-decoration: underline; }
	.new { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: var(--accent); margin-right: 6px; vertical-align: 0.12em; }
	.star { background: none; border: 0; cursor: pointer; color: var(--line); padding: 0 2px; display: flex; }
	.star.on { color: var(--star); }
	.star.on :global(svg) { fill: currentColor; }
	.meta { margin: 2px 0 8px; color: var(--muted); font-size: 0.88rem; }
	.kind { color: var(--fg); }
	.bar { height: 6px; border-radius: 3px; background: var(--chip); cursor: pointer; position: relative; overflow: hidden; }
	.bar::before { content: ''; position: absolute; inset: -8px 0; }
	.fill { height: 100%; background: var(--accent); border-radius: 3px; transition: width 0.25s linear; }
	.excerpt { margin: 10px 0 0; font-size: 0.93rem; color: var(--fg); line-height: 1.45;
		display: -webkit-box; -webkit-line-clamp: 3; line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
	.chips { margin-top: 10px; }
	@media (prefers-reduced-motion: reduce) { .fill { transition: none; } }
</style>
