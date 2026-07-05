<script lang="ts">
	import { goto } from '$app/navigation';
	import { api } from '$lib/api';
	import { panel } from '$lib/panel.svelte';
	import type { SearchResult } from '$lib/api';

	let { data } = $props();
	let q = $state(data.q);
	let results = $state<SearchResult[]>(data.results);
	let loading = $state(false);

	// Sync when data changes (e.g. sidebar search navigates to /search while already on /search)
	$effect(() => {
		q = data.q;
		results = data.results;
		loading = false;
	});

	const FIELD_LABELS: Record<string, string> = {
		book_title: 'Title',
		ocr_text: 'OCR',
		ocr_corrected: 'Correction',
		user_notes: 'Notes',
		highlights: 'Highlight',
	};

	function highlight(text: string, query: string): string {
		if (!text || !query) return escape(text);
		const re = new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi');
		return escape(text).replace(re, m => `<mark>${m}</mark>`);
	}

	function escape(s: string): string {
		return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
	}

	async function doSearch() {
		if (!q.trim()) return;
		loading = true;
		goto(`/search?q=${encodeURIComponent(q)}`, { replaceState: true });
		results = await api.search(q).catch(() => []);
		loading = false;
	}

	function keydown(e: KeyboardEvent) {
		if (e.key === 'Enter') doSearch();
	}
</script>

<svelte:head><title>Search — xteink</title></svelte:head>

<div style="display:flex;gap:.5rem;margin-bottom:1.5rem;align-items:center">
	<input
		type="text"
		bind:value={q}
		onkeydown={keydown}
		placeholder="Search OCR, notes, highlights…"
		style="flex:1;font-size:14px"
	/>
	<button onclick={doSearch} disabled={loading} style="white-space:nowrap">
		{loading ? 'Searching…' : 'Search'}
	</button>
</div>

{#if data.q && !loading}
	{#if results.length === 0}
		<p class="empty">No results for "{data.q}"</p>
	{:else}
		<p style="font-size:12px;color:var(--text-muted);margin-bottom:1rem">
			{results.length} result{results.length === 1 ? '' : 's'} for "{data.q}"
		</p>
		<div style="display:flex;flex-direction:column;gap:.75rem">
			{#each results as r}
				<div
					class="search-result"
					onclick={() => panel.open(r.id, results.map(x => x.id))}
					role="button"
					tabindex="0"
					onkeydown={(e) => e.key === 'Enter' && panel.open(r.id, [])}
				>
					<div class="search-thumb">
						<img src={api.screenshots.imageUrl(r.id)} alt="" loading="lazy" />
					</div>
					<div class="search-body">
						<div class="search-title">
							{r.book_title}
							<span style="font-weight:400;color:var(--text-muted)"> · {r.sync_date}</span>
						</div>
						{#if r.snippet}
							<p class="search-snippet">{@html highlight(r.snippet, data.q)}</p>
						{/if}
						<div class="search-fields">
							{#each r.match_fields as f}
								<span class="search-tag">{FIELD_LABELS[f] ?? f}</span>
							{/each}
						</div>
					</div>
				</div>
			{/each}
		</div>
	{/if}
{/if}

<style>
	.search-result {
		display: flex;
		gap: .75rem;
		background: var(--bg-card);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: .75rem;
		cursor: pointer;
		align-items: flex-start;
	}
	.search-result:hover { border-color: var(--text-muted); }
	.search-thumb {
		flex: 0 0 90px;
		height: 68px;
		overflow: hidden;
		border-radius: 2px;
		background: var(--bg-sidebar);
	}
	.search-thumb img { width: 100%; height: 100%; object-fit: cover; }
	.search-body { flex: 1; min-width: 0; }
	.search-title { font-size: 13px; font-weight: 600; margin-bottom: .3rem; }
	.search-snippet {
		font-family: var(--font-serif);
		font-size: 12px;
		line-height: 1.6;
		color: var(--text-muted);
		margin-bottom: .4rem;
		display: -webkit-box;
		-webkit-line-clamp: 3;
		-webkit-box-orient: vertical;
		overflow: hidden;
	}
	:global(.search-snippet mark) {
		background: #fff176;
		border-radius: 2px;
	}
	@media (prefers-color-scheme: dark) {
		:global(.search-snippet mark) { background: #6b5900; color: #fff; }
	}
	.search-fields { display: flex; gap: .3rem; flex-wrap: wrap; }
	.search-tag {
		font-size: 10px;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: .04em;
		color: var(--text-muted);
		background: var(--active-bg);
		padding: .1rem .35rem;
		border-radius: 2px;
	}
</style>
