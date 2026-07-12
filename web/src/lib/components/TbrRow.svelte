<script lang="ts">
	import type { TbrBook } from '$lib/api';

	let { book, oncycle, onremove }: {
		book: TbrBook;
		oncycle: () => void;
		onremove: () => void;
	} = $props();

	const STATUS_ICON: Record<string, string> = {
		queued: '○',
		reading: '◑',
		done: '●',
	};
</script>

<div class="tbr-row">
	<button class="status-btn" onclick={oncycle} title="Cycle status: {book.status}">
		<span class="status-icon status-{book.status}">{STATUS_ICON[book.status] ?? '○'}</span>
	</button>
	<div class="tbr-body">
		<div class="tbr-title">{book.title}</div>
		{#if book.author}
			<div class="tbr-author">{book.author}</div>
		{/if}
		{#if book.notes}
			<div class="tbr-notes">{book.notes}</div>
		{/if}
		{#if book.source_url}
			<a href={book.source_url} target="_blank" rel="noopener" class="tbr-url">↗ source</a>
		{/if}
	</div>
	<button class="remove-btn" onclick={onremove} title="Remove">×</button>
</div>

<style>
	.tbr-row {
		display: flex;
		align-items: flex-start;
		gap: .6rem;
		padding: .5rem 0;
		border-bottom: 1px solid var(--border);
	}
	.status-btn {
		border: none;
		background: none;
		padding: .1rem;
		cursor: pointer;
		line-height: 1;
		font-size: 16px;
		flex: 0 0 auto;
		margin-top: .05rem;
	}
	.status-icon { display: block; }
	.status-queued  { color: var(--text-muted); }
	.status-reading { color: #8aadf4; }
	.status-done    { color: var(--text-muted); opacity: .5; }
	.tbr-body { flex: 1; min-width: 0; }
	.tbr-title { font-size: 13px; font-weight: 600; line-height: 1.4; }
	.tbr-author { font-size: 12px; color: var(--text-muted); }
	.tbr-notes { font-size: 12px; color: var(--text-muted); margin-top: .2rem; font-style: italic; }
	.tbr-url { font-size: 11px; color: var(--link); }
	.remove-btn {
		border: none;
		background: none;
		color: var(--text-muted);
		font-size: 16px;
		cursor: pointer;
		padding: 0 .2rem;
		opacity: 0;
		transition: opacity .15s;
	}
	.tbr-row:hover .remove-btn { opacity: 1; }
</style>
