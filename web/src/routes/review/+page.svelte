<script lang="ts">
	import { goto } from '$app/navigation';
	import { api } from '$lib/api';
	import { panel } from '$lib/panel.svelte';
	import type { ReviewItem, ReviewStrategy } from '$lib/api';

	let { data } = $props();

	let items = $state<ReviewItem[]>([]);
	let cursor = $state(0);
	let busy = $state(false);
	let reviewed = $state(0);

	// A fresh queue restarts the walk, so switching strategy does not land
	// mid-way through the previous one.
	$effect(() => {
		items = data.queue?.items ?? [];
		cursor = 0;
		reviewed = 0;
	});

	let current = $derived(items[cursor] ?? null);
	let remaining = $derived(Math.max(items.length - cursor, 0));

	async function rate(rating: 'good' | 'again') {
		if (!current || busy) return;
		busy = true;
		try {
			await api.reviewSeen(current.kind, current.ref_id, rating);
			reviewed += 1;
		} catch {
			// A failed write must not strand the user on the same passage.
		} finally {
			busy = false;
			cursor += 1;
		}
	}

	function skip() {
		cursor += 1;
	}

	function openSource() {
		if (current?.screenshot_id !== null && current?.screenshot_id !== undefined) {
			panel.open(current.screenshot_id, []);
		}
	}

	function setStrategy(strategy: ReviewStrategy) {
		goto(`/review?strategy=${strategy}`, { invalidateAll: true });
	}
</script>

<svelte:head><title>Review — xteink</title></svelte:head>

<div class="review-head">
	<h1 class="page-title" style="margin-bottom:0;flex:1">Review</h1>
	<div class="review-toggle" role="group" aria-label="Review strategy">
		<button
			class:active={data.strategy === 'spaced'}
			onclick={() => setStrategy('spaced')}
		>Spaced</button>
		<button
			class:active={data.strategy === 'random'}
			onclick={() => setStrategy('random')}
		>Random</button>
	</div>
</div>

{#if !data.queue}
	<p class="empty">The review queue could not be loaded.</p>
{:else if current}
	<p class="review-count">
		{remaining} left in this pass · {data.queue.total_candidates} in the archive
	</p>

	<article class="review-card">
		<p class="review-text">{current.text}</p>
		<p class="review-meta">
			<span class="review-kind">{current.kind}</span>
			{#if current.book_title}
				<a href="/books/{encodeURIComponent(current.book_title)}">{current.book_title}</a>
			{/if}
			{#if current.date}<span>· {current.date}</span>{/if}
			{#if current.shown_count > 0}<span>· seen {current.shown_count}×</span>{/if}
		</p>
	</article>

	<div class="review-actions">
		<button onclick={() => rate('again')} disabled={busy}>Again</button>
		<button onclick={() => rate('good')} disabled={busy}>Good</button>
		<button class="review-skip" onclick={skip} disabled={busy}>Skip</button>
		{#if current.screenshot_id !== null}
			<button class="review-skip" onclick={openSource}>Open page</button>
		{/if}
	</div>
{:else}
	<p class="review-done">
		{#if reviewed > 0}
			Reviewed {reviewed} passage{reviewed === 1 ? '' : 's'}. Nothing else is due.
		{:else}
			Nothing is due for review right now.
		{/if}
	</p>
	<p><a href="/books">Back to books</a></p>
{/if}

<style>
	.review-head {
		display: flex;
		align-items: baseline;
		gap: .75rem;
		margin-bottom: 1rem;
	}
	.review-toggle { display: flex; gap: .3rem; }
	.review-toggle button { font-size: 12px; padding: .25rem .6rem; }
	.review-toggle button.active { background: var(--active-bg); border-color: var(--text-muted); }
	.review-count { font-size: 12px; color: var(--text-muted); margin-bottom: .75rem; }
	.review-card {
		padding: 1.25rem 1.4rem;
		border: 1px solid var(--border);
		border-left: 3px solid var(--mark-stroke);
		border-radius: var(--radius);
		background: var(--bg-card);
		margin-bottom: 1rem;
	}
	.review-text {
		font-family: var(--font-serif);
		font-size: 17px;
		line-height: 1.65;
		white-space: pre-wrap;
	}
	.review-meta {
		display: flex;
		flex-wrap: wrap;
		gap: .4rem;
		align-items: center;
		font-size: 12px;
		color: var(--text-muted);
		margin-top: .9rem;
	}
	.review-kind {
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0 .35rem;
		text-transform: uppercase;
		font-size: 10px;
		letter-spacing: .06em;
	}
	.review-actions { display: flex; flex-wrap: wrap; gap: .5rem; }
	.review-skip { color: var(--text-muted); }
	.review-done { font-size: 15px; margin-bottom: .75rem; }
</style>
