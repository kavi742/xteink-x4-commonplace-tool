<script lang="ts">
	import type { Book } from '$lib/api';
	let { book }: { book: Book } = $props();

	let isCollection = $derived(book.kind === 'articles');
	let href = $derived(
		isCollection ? '/articles' : `/books/${encodeURIComponent(book.book_title)}`
	);
	let articleCount = $derived(book.article_count ?? 0);

	// A book read on a device that takes no screenshots has only a reading date.
	let when = $derived(
		book.last_date ??
		(book.last_read_at ? new Date(book.last_read_at * 1000).toLocaleDateString() : null)
	);

	// Recorded by URL rather than as a flag so a card reused for another book retries that book's cover.
	let failedUrl = $state<string | null>(null);
	let coverUrl = $derived(book.cover_url !== failedUrl ? book.cover_url : null);

	function hideBrokenCover() {
		failedUrl = book.cover_url;
	}
</script>

<a {href} class="book-card">
	{#if coverUrl}
		<img
			class="book-card-cover"
			src={coverUrl}
			alt={book.book_title}
			loading="lazy"
			decoding="async"
			onerror={hideBrokenCover}
		/>
	{/if}
	<div class="book-card-body">
		<div class="book-card-title">
			{book.book_title}
			{#if isCollection}<span class="book-card-tag">collection</span>{/if}
		</div>
		<div class="book-card-meta">
			{#if book.screenshot_count > 0}
				{book.screenshot_count} screenshot{book.screenshot_count === 1 ? '' : 's'}
			{:else}
				no screenshots
			{/if}
			{#if isCollection}
				· {articleCount} article{articleCount === 1 ? '' : 's'}
			{:else if book.percentage_display !== null}
				· {book.percentage_display}%
			{/if}
			{#if when} · {when}{/if}
		</div>
	</div>
</a>

<style>
	.book-card-tag {
		display: inline-block;
		margin-left: .35rem;
		padding: 0 .3rem;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		font-size: 9px;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: .06em;
		color: var(--text-muted);
		vertical-align: middle;
	}
</style>
