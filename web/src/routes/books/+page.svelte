<script lang="ts">
	import BookCard from '$lib/components/BookCard.svelte';
	import OnThisDay from '$lib/components/OnThisDay.svelte';
	let { data } = $props();
	let { books, onThisDay } = $derived(data);
</script>

<svelte:head><title>Books — xteink</title></svelte:head>

<h1 class="page-title">Books</h1>

{#if onThisDay}
	<OnThisDay data={onThisDay} />
{/if}

{#if books.length === 0}
	<p class="empty">No books yet. Sync reading progress from a device, or press File Transfer on the X4 to archive screenshots.</p>
{:else}
	<div class="gallery">
		{#each books as book}
			<div class="gallery-item">
				<BookCard {book} />
			</div>
		{/each}
	</div>
{/if}
