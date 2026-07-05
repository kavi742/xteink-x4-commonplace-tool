<script lang="ts">
	import BookCard from '$lib/components/BookCard.svelte';
	let { data } = $props();
	let { books } = $derived(data);
</script>

<h1 class="page-title">Books</h1>

{#if books.length === 0}
	<p class="empty">No books archived yet. Press File Transfer on the X4 to sync.</p>
{:else}
	<div class="gallery">
		{#each books as book}
			<div class="gallery-item">
				<BookCard {book} />
			</div>
		{/each}
	</div>
{/if}
