<script>
	import { toasts, dismiss } from '#lib/session.svelte.js';
	import Icon from './Icon.svelte';
</script>

<div class="toasts" aria-live="polite">
	{#each toasts as t (t.id)}
		<div class="toast {t.kind}" role={t.kind === 'err' ? 'alert' : 'status'}>
			<span>{t.message}</span>
			{#if t.action}
				<button class="btn small" onclick={() => { t.action.run(); dismiss(t.id); }}>{t.action.label}</button>
			{/if}
			<button class="close" onclick={() => dismiss(t.id)} aria-label="Dismiss"><Icon name="x" size={16} /></button>
		</div>
	{/each}
</div>

<style>
	.toasts {
		position: fixed; left: 50%; transform: translateX(-50%); bottom: calc(var(--nav-h) + 16px);
		z-index: 50; display: flex; flex-direction: column; gap: 8px; width: min(520px, calc(100vw - 32px));
	}
	.toast {
		display: flex; align-items: center; gap: 10px; padding: 10px 10px 10px 14px; border-radius: 12px;
		background: var(--fg); color: var(--bg); box-shadow: 0 6px 24px rgb(0 0 0 / 0.18); font-size: 0.95rem;
		animation: up 0.18s ease-out;
	}
	.toast.err { background: var(--bad); color: #fff; }
	.toast span { flex: 1; }
	.toast .btn { background: transparent; color: inherit; border-color: currentColor; }
	.close { background: none; border: 0; color: inherit; cursor: pointer; padding: 4px; display: flex; opacity: 0.8; }
	@keyframes up { from { transform: translateY(8px); opacity: 0; } }
	@media (prefers-reduced-motion: reduce) { .toast { animation: none; } }
</style>
