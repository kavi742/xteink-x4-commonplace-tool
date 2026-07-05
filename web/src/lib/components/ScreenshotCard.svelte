<script lang="ts">
	import type { Screenshot } from '$lib/api';
	import { api } from '$lib/api';
	import { panel } from '$lib/panel.svelte';

	let { screenshot, siblings = [] }: { screenshot: Screenshot; siblings: number[] } = $props();
</script>

<div
	class="screenshot-card"
	class:selected={panel.id === screenshot.id}
	onclick={() => panel.open(screenshot.id, siblings)}
	role="button"
	tabindex="0"
	onkeydown={(e) => e.key === 'Enter' && panel.open(screenshot.id, siblings)}
>
	<img src={api.screenshots.imageUrl(screenshot.id)} alt="" loading="lazy" />
	{#if screenshot.ocr_corrected || screenshot.ocr_text}
		<p class="screenshot-card-ocr">{screenshot.ocr_corrected || screenshot.ocr_text}</p>
	{/if}
</div>
