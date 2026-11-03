<script lang="ts">
	import DateGroup from '$lib/components/DateGroup.svelte';
	import ReadingCalendar from '$lib/components/ReadingCalendar.svelte';
	import ClippingCard from '$lib/components/ClippingCard.svelte';
	import { api, type Screenshot, type Clipping, type HighlightWithMeta } from '$lib/api';
	import { panel } from '$lib/panel.svelte';

	let { data } = $props();
	let { book, screenshots, calendar, readingStats } = $derived(data);
	let filterQ = $state('');
	let tab = $state<'screenshots' | 'highlights' | 'clippings'>('screenshots');

	let highlights = $state<HighlightWithMeta[]>([]);
	let clippings = $state<Clipping[]>([]);
	// Kept in local state so a delete updates the list without a page reload.
	$effect(() => { highlights = data.highlights; });
	$effect(() => { clippings = data.clippings; });

	async function removeHighlight(id: number) {
		await api.highlights.delete(id);
		highlights = highlights.filter(h => h.id !== id);
	}

	async function removeClipping(id: number) {
		await api.clippings.delete(id);
		clippings = clippings.filter(c => c.id !== id);
	}

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

<div class="tabs" role="tablist">
	<button role="tab" aria-selected={tab === 'screenshots'} class:active={tab === 'screenshots'}
		onclick={() => (tab = 'screenshots')}>Screenshots <span class="tab-n">{screenshots.length}</span></button>
	<button role="tab" aria-selected={tab === 'highlights'} class:active={tab === 'highlights'}
		onclick={() => (tab = 'highlights')}>Highlights <span class="tab-n">{highlights.length}</span></button>
	<button role="tab" aria-selected={tab === 'clippings'} class:active={tab === 'clippings'}
		onclick={() => (tab = 'clippings')}>Clippings <span class="tab-n">{clippings.length}</span></button>
</div>

{#if tab === 'screenshots'}
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
{:else if tab === 'highlights'}
	{#if highlights.length === 0}
		<p class="empty">No highlights for this book. Open a screenshot and select text to highlight.</p>
	{:else}
		<div style="display:flex;flex-direction:column;gap:.5rem">
			{#each highlights as h}
				<div
					class="highlight-entry"
					onclick={() => panel.open(h.screenshot_id, highlights.map(x => x.screenshot_id))}
					role="button"
					tabindex="0"
					onkeydown={(e) => e.key === 'Enter' && panel.open(h.screenshot_id, [])}
				>
					<div class="highlight-thumb">
						{#if h.vault_png_path}
							<img src={api.screenshots.imageUrl(h.screenshot_id)} alt="" loading="lazy" />
						{/if}
					</div>
					<div class="highlight-body">
						<mark class="highlight-text">{h.selected_text}</mark>
						<p class="highlight-meta">{h.sync_date}</p>
					</div>
					<button
						class="highlight-del"
						onclick={(e) => { e.stopPropagation(); removeHighlight(h.id); }}
						title="Remove"
					>×</button>
				</div>
			{/each}
		</div>
	{/if}
{:else}
	{#if clippings.length === 0}
		<p class="empty">
			No clippings for this book. Save an excerpt on the reader, then turn on
			Settings &rsaquo; KOReader Sync &rsaquo; Sync Clippings and sync.
		</p>
	{:else}
		<div style="display:flex;flex-direction:column;gap:.5rem">
			{#each clippings as c}
				<ClippingCard clipping={c} onDelete={() => removeClipping(c.id)} />
			{/each}
		</div>
	{/if}
{/if}

<style>
	.reading-stats { display: flex; flex-wrap: wrap; gap: 1.75rem; margin-bottom: 1.5rem; padding: 1rem 1.1rem; background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; }
	.rs-stat { display: flex; flex-direction: column; }
	.rs-num { font-size: 22px; font-weight: 700; color: var(--text); line-height: 1.15; }
	.rs-sub { font-size: 14px; font-weight: 500; color: var(--text-muted); }
	.rs-cap { font-size: 10px; color: var(--text-muted); text-transform: uppercase; letter-spacing: .05em; }

	.tabs { display: flex; gap: .25rem; border-bottom: 1px solid var(--border); margin-bottom: 1.25rem; }
	.tabs button {
		background: none;
		border: none;
		border-bottom: 2px solid transparent;
		color: var(--text-muted);
		font-size: 13px;
		padding: .5rem .8rem;
		cursor: pointer;
		margin-bottom: -1px;
	}
	.tabs button:hover { color: var(--text); }
	.tabs button.active { color: var(--text); border-bottom-color: var(--text); }
	.tab-n { font-size: 11px; color: var(--text-muted); margin-left: .15rem; }
</style>
