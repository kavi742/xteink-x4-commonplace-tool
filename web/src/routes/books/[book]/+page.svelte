<script lang="ts">
	import DateGroup from '$lib/components/DateGroup.svelte';
	import type { Screenshot } from '$lib/api';

	let { data } = $props();
	let { book, screenshots } = $derived(data);

	let grouped = $derived.by(() => {
		const map = new Map<string, Screenshot[]>();
		for (const s of screenshots) {
			const d = s.sync_date || 'Unknown';
			if (!map.has(d)) map.set(d, []);
			map.get(d)!.push(s);
		}
		return [...map.entries()].sort((a, b) => b[0].localeCompare(a[0]));
	});

	let allIds = $derived(screenshots.map(s => s.id));
</script>

<h1 class="page-title">{book}</h1>

{#if screenshots.length === 0}
	<p class="empty">No screenshots for this book.</p>
{:else}
	{#each grouped as [date, shots]}
		<DateGroup {date} screenshots={shots} siblings={allIds} />
	{/each}
{/if}
