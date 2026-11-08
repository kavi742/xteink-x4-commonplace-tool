<script lang="ts">
	import { goto } from '$app/navigation';
	import { api } from '$lib/api';
	import { panel } from '$lib/panel.svelte';
	import type { SearchHit, SearchKind, SearchResponse } from '$lib/api';

	let { data } = $props();
	let q = $state(data.q);
	let response = $state<SearchResponse>(data.response);
	let kinds = $state<SearchKind[]>(data.kinds);
	let loading = $state(false);

	$effect(() => {
		q = data.q;
		response = data.response;
		kinds = data.kinds;
		loading = false;
	});

	const KIND_LABELS: Record<SearchKind, string> = {
		screenshot: 'OCR',
		note: 'Notes',
		highlight: 'Highlights',
		clipping: 'Clippings',
		writing: 'Writing',
	};
	const KIND_ORDER: SearchKind[] = ['screenshot', 'note', 'highlight', 'clipping', 'writing'];

	const title = $derived(data.notesOnly ? 'With notes' : (data.q ? `"${data.q}"` : ''));
	const shown = $derived(
		kinds.length
			? kinds.reduce((n, k) => n + (response.facets[k] ?? 0), 0)
			: response.total,
	);
	const siblings = $derived(
		response.results
			.map((hit) => hit.screenshot_id)
			.filter((id): id is number => id !== null),
	);

	function urlFor(nextKinds: SearchKind[]): string {
		const params = new URLSearchParams();
		if (q.trim()) params.set('q', q);
		if (nextKinds.length) params.set('kinds', nextKinds.join(','));
		if (data.book) params.set('book', data.book);
		return `/search?${params}`;
	}

	async function run(nextKinds: SearchKind[]) {
		loading = true;
		kinds = nextKinds;
		goto(urlFor(nextKinds), { replaceState: true });
		response = await api.search(q, { kinds: nextKinds, book: data.book }).catch(() => response);
		loading = false;
	}

	function doSearch() {
		if (!q.trim() && kinds.length === 0) return;
		run(kinds);
	}

	function toggle(kind: SearchKind) {
		run(kinds.includes(kind) ? kinds.filter((k) => k !== kind) : [...kinds, kind]);
	}

	function keydown(e: KeyboardEvent) {
		if (e.key === 'Enter') doSearch();
	}

	function openHit(hit: SearchHit) {
		if (hit.screenshot_id !== null) panel.open(hit.screenshot_id, siblings);
	}
</script>

<svelte:head><title>Search — xteink</title></svelte:head>

{#if !data.notesOnly}
<div style="display:flex;gap:.5rem;margin-bottom:1rem;align-items:center">
	<input
		type="text"
		bind:value={q}
		onkeydown={keydown}
		placeholder="Search OCR, notes, highlights, clippings…"
		style="flex:1;font-size:14px"
	/>
	<button onclick={doSearch} disabled={loading} style="white-space:nowrap">
		{loading ? 'Searching…' : 'Search'}
	</button>
</div>
{:else}
	<div style="display:flex;align-items:center;gap:.75rem;margin-bottom:1rem">
		<h1 class="page-title" style="margin-bottom:0">📝 With notes</h1>
		<a href="/books" style="font-size:12px;color:var(--text-muted)">← all books</a>
	</div>
{/if}

{#if response.total > 0 || kinds.length > 0}
	<div class="facets">
		{#each KIND_ORDER as kind}
			<button class="facet" class:on={kinds.includes(kind)} onclick={() => toggle(kind)}>
				{KIND_LABELS[kind]} <span class="facet-count">{response.facets[kind] ?? 0}</span>
			</button>
		{/each}
	</div>
{/if}

{#if (data.q || kinds.length > 0) && !loading}
	{#if response.results.length === 0}
		<p class="empty">No results{data.notesOnly ? ' — no screenshots with notes yet.' : ` for "${data.q}"`}</p>
	{:else}
		<p style="font-size:12px;color:var(--text-muted);margin-bottom:1rem">
			{shown} result{shown === 1 ? '' : 's'}{title ? ` for ${title}` : ''}
		</p>
		<div style="display:flex;flex-direction:column;gap:.75rem">
			{#each response.results as hit}
				{#if hit.screenshot_id !== null}
					<div
						class="search-result"
						onclick={() => openHit(hit)}
						role="button"
						tabindex="0"
						onkeydown={(e) => e.key === 'Enter' && openHit(hit)}
					>
						<div class="search-thumb">
							<img src={api.screenshots.imageUrl(hit.screenshot_id)} alt="" loading="lazy" />
						</div>
						<div class="search-body">
							<div class="search-title">
								{hit.book_title}
								<span style="font-weight:400;color:var(--text-muted)"> · {hit.date}</span>
							</div>
							<!-- Escaped server-side; <mark> is the only markup SQLite emits. -->
							<p class="search-snippet">{@html hit.snippet}</p>
							<div class="search-fields">
								<span class="search-tag">{KIND_LABELS[hit.kind]}</span>
							</div>
						</div>
					</div>
				{:else}
					<a class="search-result" href="/books/{hit.book_slug}">
						<div class="search-body">
							<div class="search-title">
								{hit.book_title || 'Unknown book'}
								<span style="font-weight:400;color:var(--text-muted)"> · {hit.date}</span>
							</div>
							<p class="search-snippet">{@html hit.snippet}</p>
							<div class="search-fields">
								<span class="search-tag">{KIND_LABELS[hit.kind]}</span>
							</div>
						</div>
					</a>
				{/if}
			{/each}
		</div>
	{/if}
{/if}

<style>
	.facets { display: flex; gap: .4rem; flex-wrap: wrap; margin-bottom: 1.25rem; }
	.facet {
		font-size: 11px;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: .04em;
		padding: .25rem .5rem;
		border-radius: var(--radius);
		border: 1px solid var(--border);
		background: var(--bg-card);
		color: var(--text-muted);
		cursor: pointer;
	}
	.facet.on { background: var(--active-bg); color: var(--text); border-color: var(--text-muted); }
	.facet-count { font-weight: 400; opacity: .7; }
	.search-result {
		display: flex;
		gap: .75rem;
		background: var(--bg-card);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: .75rem;
		cursor: pointer;
		align-items: flex-start;
		text-decoration: none;
		color: inherit;
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
		background: #eed49f;
		color: #24273a;
		border-radius: 2px;
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
