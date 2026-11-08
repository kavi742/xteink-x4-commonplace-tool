<script lang="ts">
	import { api } from '$lib/api';
	import { invalidateAll } from '$app/navigation';

	let { data } = $props();
	let trash = $derived(data.trash);

	type BucketKey = 'highlights' | 'clippings' | 'writing';
	type Pending = { bucket: BucketKey; id: number; name: string } | null;

	const LABELS: Record<BucketKey, string> = {
		highlights: 'Highlights',
		clippings: 'Clippings',
		writing: 'Writing',
	};

	// The writing log owns its own restore and purge routes and does not exist
	// yet, so its rows render with both actions disabled until it lands.
	const ACTIONS: Record<
		BucketKey,
		{ restore?: (id: number) => Promise<unknown>; purge?: (id: number) => Promise<unknown> }
	> = {
		highlights: { restore: api.trash.restoreHighlight, purge: api.trash.purgeHighlight },
		clippings: { restore: api.trash.restoreClipping, purge: api.trash.purgeClipping },
		writing: {},
	};

	let pending = $state<Pending>(null);
	let busy = $state(false);

	let buckets = $derived(
		(Object.keys(trash.counts) as BucketKey[])
			.filter((key) => (trash[key] ?? []).length > 0)
			.map((key) => ({ key, label: LABELS[key] ?? key, count: trash.counts[key] })),
	);

	let total = $derived(buckets.reduce((sum, b) => sum + b.count, 0));

	async function reload() {
		await invalidateAll();
	}

	async function restore(bucket: BucketKey, id: number) {
		const fn = ACTIONS[bucket].restore;
		if (!fn || busy) return;
		busy = true;
		try {
			await fn(id);
			await reload();
		} finally {
			busy = false;
		}
	}

	function askPurge(bucket: BucketKey, id: number, name: string) {
		pending = { bucket, id, name };
	}

	function cancelPurge() {
		pending = null;
	}

	async function confirmPurge() {
		const target = pending;
		const fn = target ? ACTIONS[target.bucket].purge : undefined;
		if (!target || !fn || busy) return;
		busy = true;
		try {
			await fn(target.id);
			pending = null;
			await reload();
		} finally {
			busy = false;
		}
	}

	function purgeWarning(bucket: BucketKey): string {
		if (bucket === 'clippings') {
			return 'The excerpt is cleared for good. The tombstone row is kept so the reader does not bring the id back on its next sync.';
		}
		return 'This cannot be undone.';
	}
</script>

<svelte:head><title>Trash — xteink</title></svelte:head>

<h1 class="page-title">Trash</h1>

{#if total === 0}
	<p class="empty">Nothing in the trash. Deleted highlights and clippings land here.</p>
{:else}
	{#each buckets as bucket (bucket.key)}
		<section class="bucket">
			<p class="date-heading">
				{bucket.label}
				<span style="font-weight:400;margin-left:.5rem">({bucket.count})</span>
			</p>

			{#if bucket.key === 'highlights'}
				{#each trash.highlights as row (row.id)}
					<article class="trash-row">
						<div class="trash-body">
							<blockquote class="trash-text">{row.selected_text}</blockquote>
							<p class="trash-meta">
								{row.book_title || 'Unknown book'}
								{#if row.deleted_at} · deleted {row.deleted_at}{/if}
							</p>
						</div>
						<div class="trash-actions">
							<button onclick={() => restore('highlights', row.id)} disabled={busy}>Restore</button>
							<button
								class="danger"
								onclick={() => askPurge('highlights', row.id, row.selected_text)}
								disabled={busy}>Purge</button
							>
						</div>
					</article>
				{/each}
			{:else if bucket.key === 'clippings'}
				{#each trash.clippings as row (row.id)}
					<article class="trash-row">
						<div class="trash-body">
							{#if row.chapter}<p class="trash-chapter">{row.chapter}</p>{/if}
							<blockquote class="trash-text">
								{row.restorable ? row.text : 'Text was discarded by an older tombstone.'}
							</blockquote>
							<p class="trash-meta">
								{row.book_title || 'Unmapped book'}
								{#if row.deleted_source} · deleted on the {row.deleted_source === 'device' ? 'reader' : 'web'}{/if}
								{#if row.deleted_at} · {row.deleted_at}{/if}
							</p>
						</div>
						<div class="trash-actions">
							<button
								onclick={() => restore('clippings', row.id)}
								disabled={busy || !row.restorable}
								title={row.restorable
									? 'Restore. The reader picks it up at its next sync.'
									: 'Its text was discarded by a tombstone written before soft delete, so there is nothing to restore.'}
								>Restore</button
							>
							<button
								class="danger"
								onclick={() => askPurge('clippings', row.id, row.text || row.clip_id)}
								disabled={busy}>Purge</button
							>
						</div>
					</article>
				{/each}
			{:else if bucket.key === 'writing'}
				{#each trash.writing as row (row.id)}
					<article class="trash-row">
						<div class="trash-body">
							<p class="trash-chapter">{row.title}</p>
							<blockquote class="trash-text">{row.excerpt}</blockquote>
							<p class="trash-meta">
								{row.book_title || 'Unknown book'} · {row.ref_count} citations
								{#if row.deleted_at} · deleted {row.deleted_at}{/if}
							</p>
						</div>
						<div class="trash-actions">
							<button disabled title="Restoring an entry arrives with the writing log.">Restore</button>
							<button class="danger" disabled title="Purging an entry arrives with the writing log.">Purge</button>
						</div>
					</article>
				{/each}
			{/if}
		</section>
	{/each}
{/if}

{#if pending}
	<div class="confirm-backdrop" role="dialog" aria-modal="true" aria-label="Confirm purge">
		<div class="confirm">
			<p class="confirm-title">Purge permanently?</p>
			<blockquote class="trash-text">{pending.name}</blockquote>
			<p class="confirm-note">{purgeWarning(pending.bucket)}</p>
			<div class="trash-actions">
				<button onclick={cancelPurge} disabled={busy}>Cancel</button>
				<button class="danger" onclick={confirmPurge} disabled={busy}>Purge permanently</button>
			</div>
		</div>
	</div>
{/if}

<style>
	.bucket { margin-bottom: 2rem; }
	.trash-row {
		display: flex;
		align-items: flex-start;
		gap: 1rem;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--bg-card);
		padding: .75rem .9rem;
		margin-bottom: .5rem;
	}
	.trash-body { flex: 1; min-width: 0; }
	.trash-text {
		margin: 0;
		font-family: var(--font-serif);
		font-size: 14px;
		line-height: 1.6;
		white-space: pre-wrap;
		border-left: 3px solid var(--text-muted);
		padding-left: .75rem;
	}
	.trash-chapter { margin: 0 0 .4rem; font-size: 13px; font-weight: 600; color: var(--text); }
	.trash-meta { font-size: 11px; color: var(--text-muted); margin: .5rem 0 0; }
	.trash-actions { display: flex; gap: .4rem; flex-shrink: 0; }
	.trash-actions button { font-size: 12px; padding: .25rem .6rem; }
	.trash-actions button:disabled { opacity: .5; cursor: default; }
	.danger { color: var(--danger); }

	.confirm-backdrop {
		position: fixed;
		inset: 0;
		z-index: 80;
		display: flex;
		align-items: center;
		justify-content: center;
		background: oklch(0% 0 0 / 0.45);
		padding: 1rem;
	}
	.confirm {
		max-width: 28rem;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--bg-card);
		padding: 1rem;
		box-shadow: 0 4px 14px var(--shadow-color);
	}
	.confirm-title { margin: 0 0 .5rem; font-weight: 600; }
	.confirm-note { font-size: 12px; color: var(--text-muted); margin: .6rem 0 .9rem; }
</style>
