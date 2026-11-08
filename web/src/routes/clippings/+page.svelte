<script lang="ts">
	import { api } from '$lib/api';
	import ClippingCard from '$lib/components/ClippingCard.svelte';
	import ExportMenu from '$lib/components/ExportMenu.svelte';
	import { undo } from '$lib/undo.svelte';
	import type { Clipping } from '$lib/api';

	type Group = { title: string; mapped: boolean; kind: 'book' | 'article'; items: Clipping[] };

	let { data } = $props();
	let clippings = $state<Clipping[]>(data.clippings);
	// Kept in local state so a delete updates the list without a page reload,
	// and resynced so an undo elsewhere in the app is reflected here.
	$effect(() => { clippings = data.clippings; });

	let grouped = $derived.by(() => {
		const map = new Map<string, Group>();
		for (const c of clippings) {
			const kind: 'book' | 'article' = c.kind === 'article' ? 'article' : 'book';
			const title = c.book_title || (kind === 'article' ? 'Unmapped article' : 'Unmapped book');
			const key = `${kind}:${title}`;
			if (!map.has(key)) map.set(key, { title, mapped: !!c.book_title, kind, items: [] });
			map.get(key)!.items.push(c);
		}
		return [...map.values()].sort((a, b) => a.title.localeCompare(b.title));
	});

	let bookGroups = $derived(grouped.filter(g => g.kind === 'book'));
	let articleGroups = $derived(grouped.filter(g => g.kind === 'article'));

	function groupHref(g: Group): string | undefined {
		if (!g.mapped) return undefined;
		return g.kind === 'article' ? '/articles' : `/books/${encodeURIComponent(g.title)}`;
	}

	async function remove(id: number) {
		const res = await api.clippings.delete(id);
		clippings = clippings.filter(c => c.id !== id);
		undo.offerToken(res.undo_token);
	}
</script>

{#snippet groupList(groups: Group[])}
	{#each groups as g}
		{@const href = groupHref(g)}
		<section style="margin-bottom:2rem">
			<p class="date-heading">
				{#if href}
					<a {href} style="color:inherit">{g.title}</a>
				{:else}
					<span>{g.title}</span>
				{/if}
				<span style="font-weight:400;margin-left:.5rem">({g.items.length})</span>
				{#if g.mapped && g.kind === 'book'}
					<span style="float:right">
						<ExportMenu slug={g.title} defaultInclude={['clippings']} />
					</span>
				{/if}
			</p>
			<div style="display:flex;flex-direction:column;gap:.5rem">
				{#each g.items as c}
					<ClippingCard clipping={c} {href} onDelete={() => remove(c.id)} />
				{/each}
			</div>
		</section>
	{/each}
{/snippet}

<svelte:head><title>Clippings — xteink</title></svelte:head>

<h1 class="page-title">Clippings</h1>

{#if clippings.length === 0}
	<p class="empty">
		No clippings yet. Save an excerpt on the reader, then turn on
		Settings &rsaquo; KOReader Sync &rsaquo; Sync Clippings and sync.
	</p>
{:else}
	{#if bookGroups.length > 0}
		<p class="kind-heading">From books</p>
		{@render groupList(bookGroups)}
	{/if}
	{#if articleGroups.length > 0}
		<p class="kind-heading">From articles</p>
		{@render groupList(articleGroups)}
	{/if}
{/if}

<style>
	.kind-heading {
		font-size: 13px;
		font-weight: 700;
		color: var(--text);
		margin-bottom: 1rem;
		padding-bottom: .4rem;
		border-bottom: 2px solid var(--border);
	}
</style>
