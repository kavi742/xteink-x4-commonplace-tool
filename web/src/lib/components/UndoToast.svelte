<script lang="ts">
	import { undo } from '$lib/undo.svelte';
	import { invalidateAll } from '$app/navigation';

	async function runUndo() {
		await undo.run();
		await invalidateAll();
	}

	function dismiss() {
		undo.dismiss();
	}
</script>

{#if undo.entry}
	<div class="undo-toast" role="status" aria-live="polite">
		<span class="undo-label">{undo.entry.label}</span>
		<button class="undo-action" onclick={runUndo} disabled={undo.busy}>Undo</button>
		<button class="undo-close" onclick={dismiss} aria-label="Dismiss">×</button>
	</div>
{/if}

<style>
	.undo-toast {
		position: fixed;
		left: 50%;
		bottom: 1.5rem;
		transform: translateX(-50%);
		z-index: 70;
		display: flex;
		align-items: center;
		gap: .75rem;
		max-width: min(36rem, calc(100vw - 2rem));
		padding: .6rem .75rem .6rem 1rem;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--bg-card);
		color: var(--text);
		box-shadow: 0 4px 14px var(--shadow-color);
	}
	.undo-label { font-size: 13px; line-height: 1.4; }
	.undo-action {
		flex-shrink: 0;
		background: none;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		color: var(--link);
		font-size: 12px;
		font-weight: 600;
		padding: .25rem .6rem;
		cursor: pointer;
	}
	.undo-action:hover:not(:disabled) { color: var(--link-hover); border-color: var(--text-muted); }
	.undo-action:disabled { opacity: .5; cursor: default; }
	.undo-close {
		flex-shrink: 0;
		background: none;
		border: none;
		color: var(--text-muted);
		font-size: 16px;
		line-height: 1;
		padding: .1rem .25rem;
		cursor: pointer;
	}
	.undo-close:hover { color: var(--text); }

	@media (max-width: 700px) {
		.undo-toast { bottom: 4.5rem; }
	}
</style>
