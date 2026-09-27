<script lang="ts">
	import type { Book } from '$lib/api';
	let { book }: { book: Book } = $props();

	// A book read on a device that takes no screenshots has only a reading date.
	let when = $derived(
		book.last_date ??
		(book.last_read_at ? new Date(book.last_read_at * 1000).toLocaleDateString() : null)
	);
</script>

<a href="/books/{encodeURIComponent(book.book_title)}" class="book-card">
	<div class="book-card-body">
		<div class="book-card-title">{book.book_title}</div>
		<div class="book-card-meta">
			{#if book.screenshot_count > 0}
				{book.screenshot_count} screenshot{book.screenshot_count === 1 ? '' : 's'}
			{:else}
				no screenshots
			{/if}
			{#if book.percentage_display !== null} · {book.percentage_display}%{/if}
			{#if when} · {when}{/if}
		</div>
	</div>
</a>
