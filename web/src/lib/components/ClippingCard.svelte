<script lang="ts">
	import type { Clipping } from '$lib/api';
	import { panel } from '$lib/panel.svelte';

	let {
		clipping,
		onDelete,
		href,
	}: { clipping: Clipping; onDelete?: () => void; href?: string } = $props();

	// The device stores 0 when its clock was never set; epoch dates are noise.
	let savedOn = $derived(
		clipping.created_at > 0
			? new Date(clipping.created_at * 1000).toISOString().slice(0, 10)
			: ''
	);
	// start_page/end_page are pages *within the spine item*, at whatever font
	// size that device was using — not book pages. Label them as such.
	let pageLabel = $derived(
		clipping.start_page === clipping.end_page
			? `ch. page ${clipping.start_page + 1}`
			: `ch. pages ${clipping.start_page + 1}–${clipping.end_page + 1}`
	);

	// screenshot_id wins over href: open the panel; otherwise fall back to the book link.
	let screenshotId = $derived(clipping.screenshot_id);
	let linked = $derived(screenshotId != null || !!href);

	function openScreenshot() {
		if (screenshotId != null) panel.open(screenshotId);
	}
	function onKey(e: KeyboardEvent) {
		if (e.key === 'Enter' || e.key === ' ') {
			e.preventDefault();
			openScreenshot();
		}
	}
</script>

{#snippet body()}
	{#if clipping.chapter}<p class="clip-chapter">{clipping.chapter}</p>{/if}
	<blockquote class="clip-text">{clipping.text}</blockquote>
	<p class="clip-meta">
		{pageLabel} of {clipping.pages}
		{#if clipping.words > 0} · {clipping.words} words{/if}
		{#if savedOn} · {savedOn}{/if}
		{#if screenshotId != null} · <span class="clip-shot">screenshot</span>{/if}
	</p>
{/snippet}

<article class="clip" class:linked>
	{#if screenshotId != null}
		<div
			class="clip-link"
			role="button"
			tabindex="0"
			onclick={openScreenshot}
			onkeydown={onKey}
		>{@render body()}</div>
	{:else if href}
		<a class="clip-link" {href}>{@render body()}</a>
	{:else}
		{@render body()}
	{/if}
	{#if onDelete}
		<button class="clip-del" onclick={onDelete} title="Remove clipping">×</button>
	{/if}
</article>

<style>
	.clip {
		position: relative;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--bg-card);
		padding: .75rem 2rem .6rem .9rem;
	}
	.clip.linked:hover { border-color: var(--text-muted); }
	.clip-link { display: block; color: inherit; text-decoration: none; cursor: pointer; }
	.clip-text {
		margin: 0;
		font-family: var(--font-serif);
		font-size: 14px;
		line-height: 1.6;
		white-space: pre-wrap;
		border-left: 3px solid var(--text-muted);
		padding-left: .75rem;
	}
	.clip-meta { font-size: 11px; color: var(--text-muted); margin: .5rem 0 0 .75rem; }
	.clip-chapter {
		margin: 0 0 .4rem;
		font-size: 13px;
		font-weight: 600;
		color: var(--text);
	}
	.clip-shot { text-decoration: underline; }
	.clip-del {
		position: absolute;
		top: .4rem;
		right: .4rem;
		background: none;
		border: none;
		color: var(--text-muted);
		font-size: 16px;
		line-height: 1;
		cursor: pointer;
		padding: .2rem .35rem;
	}
	.clip-del:hover { color: var(--text); }
</style>
