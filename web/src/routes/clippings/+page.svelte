<script lang="ts">
	import { api } from '$lib/api';
	import ClippingCard from '$lib/components/ClippingCard.svelte';
	import type { Clipping } from '$lib/api';

	let { data } = $props();
	let clippings = $state<Clipping[]>(data.clippings);

	let grouped = $derived.by(() => {
		const map = new Map<string, Clipping[]>();
		for (const c of clippings) {
			const key = c.book_title || 'Unmapped book';
			if (!map.has(key)) map.set(key, []);
			map.get(key)!.push(c);
		}
		return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0]));
	});

	async function remove(id: number) {
		await api.clippings.delete(id);
		clippings = clippings.filter(c => c.id !== id);
	}
</script>

<svelte:head><title>Clippings — xteink</title></svelte:head>

<h1 class="page-title">Clippings</h1>

{#if clippings.length === 0}
	<p class="empty">
		No clippings yet. Save an excerpt on the reader, then turn on
		Settings &rsaquo; KOReader Sync &rsaquo; Sync Clippings and sync.
	</p>
{:else}
	{#each grouped as [book, items]}
		<section style="margin-bottom:2rem">
			<p class="date-heading">
				{#if book === 'Unmapped book'}
					<span>{book}</span>
				{:else}
					<a href="/books/{encodeURIComponent(book)}" style="color:inherit">{book}</a>
				{/if}
				<span style="font-weight:400;margin-left:.5rem">({items.length})</span>
			</p>
			<div style="display:flex;flex-direction:column;gap:.5rem">
				{#each items as c}
					<ClippingCard
						clipping={c}
						href={c.book_title ? `/books/${encodeURIComponent(c.book_title)}` : undefined}
						onDelete={() => remove(c.id)}
					/>
				{/each}
			</div>
		</section>
	{/each}
{/if}
