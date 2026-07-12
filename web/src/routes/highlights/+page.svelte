<script lang="ts">
	import { api } from '$lib/api';
	import { panel } from '$lib/panel.svelte';
	import type { HighlightWithMeta } from '$lib/api';

	let { data } = $props();
	let highlights = $state<HighlightWithMeta[]>(data.highlights);

	// Group by book
	let grouped = $derived.by(() => {
		const map = new Map<string, HighlightWithMeta[]>();
		for (const h of highlights) {
			const key = h.book_title || 'Unknown';
			if (!map.has(key)) map.set(key, []);
			map.get(key)!.push(h);
		}
		return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0]));
	});

	async function remove(id: number) {
		await api.highlights.delete(id);
		highlights = highlights.filter(h => h.id !== id);
	}
</script>

<svelte:head><title>Highlights — xteink</title></svelte:head>

<h1 class="page-title">Highlights</h1>

{#if highlights.length === 0}
	<p class="empty">No highlights yet. Open a screenshot and select text to highlight.</p>
{:else}
	{#each grouped as [book, items]}
		<section style="margin-bottom:2rem">
			<p class="date-heading">
				<a href="/books/{encodeURIComponent(book)}" style="color:inherit">{book}</a>
				<span style="font-weight:400;margin-left:.5rem">({items.length})</span>
			</p>
			<div style="display:flex;flex-direction:column;gap:.5rem">
				{#each items as h}
					<div
						class="highlight-entry"
						onclick={() => panel.open(h.screenshot_id, items.map(x => x.screenshot_id))}
						role="button"
						tabindex="0"
						onkeydown={(e) => e.key === 'Enter' && panel.open(h.screenshot_id, [])}
					>
						<div class="highlight-thumb">
							{#if h.vault_png_path}
								<img src={api.screenshots.imageUrl(h.screenshot_id)} alt="" loading="lazy" />
							{/if}
						</div>
						<div class="highlight-body">
							<mark class="highlight-text">{h.selected_text}</mark>
							<p class="highlight-meta">{h.sync_date}</p>
						</div>
						<button
							class="highlight-del"
							onclick={(e) => { e.stopPropagation(); remove(h.id); }}
							title="Remove"
						>×</button>
					</div>
				{/each}
			</div>
		</section>
	{/each}
{/if}

<style>
	.highlight-entry {
		display: flex;
		gap: .75rem;
		align-items: flex-start;
		background: var(--bg-card);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: .6rem;
		cursor: pointer;
	}
	.highlight-entry:hover { border-color: var(--text-muted); }
	.highlight-thumb {
		flex: 0 0 80px;
		height: 60px;
		overflow: hidden;
		border-radius: 2px;
		background: var(--bg-sidebar);
	}
	.highlight-thumb img { width: 100%; height: 100%; object-fit: cover; }
	.highlight-body { flex: 1; min-width: 0; }
	.highlight-text {
		display: block;
		font-family: var(--font-serif);
		font-size: 13px;
		line-height: 1.5;
		background: #eed49f;
		color: #24273a;
		padding: .15rem .3rem;
		border-radius: 2px;
	}
	.highlight-meta { font-size: 11px; color: var(--text-muted); margin-top: .3rem; }
	.highlight-del {
		border: none;
		background: none;
		color: var(--text-muted);
		font-size: 16px;
		line-height: 1;
		padding: 0 .2rem;
		cursor: pointer;
		align-self: center;
	}
</style>
