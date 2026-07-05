<script lang="ts">
	import type { Book } from '$lib/api';
	let { book }: { book: Book } = $props();
</script>

<a href="/books/{encodeURIComponent(book.book_title)}" class="book-card">
	<div class="book-card-body">
		<div class="book-card-title">{book.book_title}</div>
		<div class="book-card-meta">
			{book.screenshot_count} screenshot{book.screenshot_count === 1 ? '' : 's'}
			{#if book.last_date} · {book.last_date}{/if}
		</div>
	</div>
</a>
