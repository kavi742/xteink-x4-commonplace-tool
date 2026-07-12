<script lang="ts">
	import DateGroup from '$lib/components/DateGroup.svelte';
	import ReadingCalendar from '$lib/components/ReadingCalendar.svelte';
	import type { Screenshot } from '$lib/api';

	let { data } = $props();
	let { book, screenshots, calendar, readingStats } = $derived(data);
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

{#if readingStats && readingStats.current_pct > 0}
	<section class="reading-stats">
		{#if readingStats.total_pages}
			<div class="rs-stat">
				<span class="rs-num">
					{readingStats.current_page}<span class="rs-sub"> / ~{readingStats.total_pages}</span>
				</span>
				<span class="rs-cap">page{readingStats.page_source === 'estimate' ? ' (est.)' : ''}</span>
			</div>
		{/if}
		<div class="rs-stat">
			<span class="rs-num">{readingStats.current_pct}%</span>
			<span class="rs-cap">{readingStats.finished ? 'finished' : 'read'}</span>
		</div>
		<div class="rs-stat">
			<span class="rs-num">{readingStats.days_read}</span>
			<span class="rs-cap">day{readingStats.days_read === 1 ? '' : 's'} read</span>
		</div>
		<div class="rs-stat">
			<span class="rs-num">{readingStats.sessions}</span>
			<span class="rs-cap">session{readingStats.sessions === 1 ? '' : 's'}</span>
		</div>
	</section>
{/if}

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

<style>
	.reading-stats { display: flex; flex-wrap: wrap; gap: 1.75rem; margin-bottom: 1.5rem; padding: 1rem 1.1rem; background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; }
	.rs-stat { display: flex; flex-direction: column; }
	.rs-num { font-size: 22px; font-weight: 700; color: var(--text); line-height: 1.15; }
	.rs-sub { font-size: 14px; font-weight: 500; color: var(--text-muted); }
	.rs-cap { font-size: 10px; color: var(--text-muted); text-transform: uppercase; letter-spacing: .05em; }
</style>
