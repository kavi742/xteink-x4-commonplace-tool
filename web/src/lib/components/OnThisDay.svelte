<script lang="ts">
	import { panel } from '$lib/panel.svelte';
	import type { OnThisDayResponse, ResurfacedItem } from '$lib/api';

	let { data }: { data: OnThisDayResponse } = $props();

	function caption(yearsAgo: number) {
		return yearsAgo === 1 ? '1 year ago' : `${yearsAgo} years ago`;
	}

	function bookHref(item: ResurfacedItem) {
		return item.book_title ? `/books/${encodeURIComponent(item.book_title)}` : '/books';
	}
</script>

{#if data.total > 0}
	<section class="otd">
		<p class="section-label">On this day</p>
		{#each data.years as bucket}
			<p class="otd-year">{bucket.year} <span class="otd-ago">{caption(bucket.years_ago)}</span></p>
			<div class="otd-items">
				{#each bucket.items as item}
					{#if item.screenshot_id !== null}
						<button
							class="otd-card"
							type="button"
							onclick={() => panel.open(item.screenshot_id as number, [])}
						>
							<span class="otd-text">{item.text}</span>
							<span class="otd-meta">{item.book_title || 'Unmapped'} · {item.date}</span>
						</button>
					{:else}
						<a class="otd-card" href={bookHref(item)}>
							<span class="otd-text">{item.text}</span>
							<span class="otd-meta">{item.book_title || 'Unmapped'} · {item.date}</span>
						</a>
					{/if}
				{/each}
			</div>
		{/each}
	</section>
{/if}

<style>
	.otd { margin-bottom: 1.75rem; }
	.otd-year {
		font-size: 13px;
		font-weight: 600;
		margin: .6rem 0 .4rem;
	}
	.otd-ago { font-weight: 400; color: var(--text-muted); margin-left: .4rem; }
	.otd-items { display: flex; flex-direction: column; gap: .4rem; }
	.otd-card {
		display: flex;
		flex-direction: column;
		gap: .3rem;
		width: 100%;
		text-align: left;
		padding: .55rem .7rem;
		border: 1px solid var(--border);
		border-left: 3px solid var(--mark-stroke);
		border-radius: var(--radius);
		background: var(--bg-card);
		color: var(--text);
	}
	.otd-card:hover { border-color: var(--text-muted); border-left-color: var(--mark-bg); }
	.otd-text { font-family: var(--font-serif); font-size: 14px; line-height: 1.5; }
	.otd-meta { font-size: 11px; color: var(--text-muted); }
</style>
