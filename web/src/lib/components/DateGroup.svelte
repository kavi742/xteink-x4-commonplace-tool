<script lang="ts">
	import type { Screenshot } from '$lib/api';
	import ScreenshotCard from './ScreenshotCard.svelte';

	let { date, screenshots, siblings = [] }: {
		date: string;
		screenshots: Screenshot[];
		siblings: number[];
	} = $props();
</script>

<section class="date-group">
	<p class="date-heading">{date}</p>
	<div class="gallery">
		{#each screenshots as shot}
			<div class="gallery-item">
				<ScreenshotCard screenshot={shot} {siblings} />
			</div>
		{/each}
	</div>
</section>
