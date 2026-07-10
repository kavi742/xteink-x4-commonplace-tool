<script lang="ts">
	import DateGroup from '$lib/components/DateGroup.svelte';
	import ReadingCalendar from '$lib/components/ReadingCalendar.svelte';
	import type { Screenshot } from '$lib/api';

	let { data } = $props();
	let { book, screenshots, calendar } = $derived(data);
	let filterQ = $state('');

	let grouped = $derived.by(() => {
		const q = filterQ.toLowerCase().trim();
		const filtered = q
			? screenshots.filter(s =>
				(s.ocr_text ?? '').toLowerCase().includes(q) ||
				(s.ocr_corrected ?? '').toLowerCase().includes(q) ||
				(s.user_notes ?? '').toLowerCase().includes(q))
			: screenshots;

		const map = new Map<string, Screenshot[]>();
		for (const s of filtered) {
			const d = s.sync_date || 'Unknown';
			if (!map.has(d)) map.set(d, []);
			map.get(d)!.push(s);
		}
		return [...map.entries()].sort((a, b) => b[0].localeCompare(a[0]));
	});

	let allIds = $derived(screenshots.map(s => s.id));
	let visibleCount = $derived(grouped.reduce((n, [, s]) => n + s.length, 0));
</script>

<div style="display:flex;align-items:baseline;gap:.75rem;margin-bottom:1.25rem">
	<h1 class="page-title" style="margin-bottom:0;flex:1">{book}</h1>
	<span style="font-size:12px;color:var(--text-muted)">{visibleCount} / {screenshots.length}</span>
</div>

{#if calendar.length > 0}
	<section style="margin-bottom:1.5rem">
		<div class="section-label">Reading calendar</div>
		<ReadingCalendar days={calendar} />
	</section>
{/if}

<div style="margin-bottom:1rem">
	<input
		type="text"
		bind:value={filterQ}
		placeholder="Filter by OCR text or notes…"
		style="font-size:13px"
	/>
</div>

{#if screenshots.length === 0}
	<p class="empty">No screenshots for this book.</p>
{:else if visibleCount === 0}
	<p class="empty">No screenshots match "{filterQ}".</p>
{:else}
	{#each grouped as [date, shots]}
		<DateGroup {date} screenshots={shots} siblings={allIds} />
	{/each}
{/if}
