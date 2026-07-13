<script lang="ts">
	import type { ProgressEntry, ReadingStats } from '$lib/api';

	let { data } = $props();
	let { entries, stats } = $derived(data);

	function cfi(progress: string) {
		const m = progress?.match(/DocFragment\[(\d+)\]/);
		return m ? `\u00a7${m[1]}` : '';
	}

	function dayKey(at: string): string {
		return new Date(at).toLocaleDateString();
	}
	function dayHeading(at: string): string {
		return new Date(at).toLocaleDateString(undefined, {
			weekday: 'long', month: 'long', day: 'numeric', year: 'numeric'
		});
	}

	// Entries arrive newest-first; group them by local calendar day.
	let grouped = $derived.by(() => {
		const map = new Map<string, { heading: string; items: ProgressEntry[] }>();
		for (const e of entries) {
			const key = dayKey(e.at);
			if (!map.has(key)) map.set(key, { heading: dayHeading(e.at), items: [] });
			map.get(key)!.items.push(e);
		}
		return [...map.values()];
	});
</script>

<svelte:head><title>Reading Log — xteink</title></svelte:head>

<h1 class="page-title">Reading Log</h1>

{#if stats}
	<div class="stats">
		{#if stats.pages_read}
			<div class="stats-group">
				<div class="stats-label">Pages read</div>
				<div class="stats-row">
					<div class="stat"><span class="stat-num">{stats.pages_read.today}</span><span class="stat-cap">today</span></div>
					<div class="stat"><span class="stat-num">{stats.pages_read.week}</span><span class="stat-cap">week</span></div>
					<div class="stat"><span class="stat-num">{stats.pages_read.month}</span><span class="stat-cap">month</span></div>
				</div>
			</div>
		{:else}
			<div class="stats-group">
				<div class="stats-label">Read</div>
				<div class="stats-row">
					<div class="stat"><span class="stat-num">{stats.read_pct.today}%</span><span class="stat-cap">today</span></div>
					<div class="stat"><span class="stat-num">{stats.read_pct.week}%</span><span class="stat-cap">week</span></div>
					<div class="stat"><span class="stat-num">{stats.read_pct.month}%</span><span class="stat-cap">month</span></div>
				</div>
			</div>
		{/if}
		<div class="stats-group">
			<div class="stats-label">Books</div>
			<div class="stats-row">
				<div class="stat"><span class="stat-num">{stats.books.started}</span><span class="stat-cap">started</span></div>
				<div class="stat"><span class="stat-num">{stats.books.in_progress}</span><span class="stat-cap">reading</span></div>
				<div class="stat"><span class="stat-num">{stats.books.finished}</span><span class="stat-cap">finished</span></div>
			</div>
		</div>
		<div class="stats-group">
			<div class="stats-label">Books Completed</div>
			<div class="stats-row">
				<div class="stat"><span class="stat-num">{stats.books_read.today}</span><span class="stat-cap">today</span></div>
				<div class="stat"><span class="stat-num">{stats.books_read.week}</span><span class="stat-cap">week</span></div>
				<div class="stat"><span class="stat-num">{stats.books_read.month}</span><span class="stat-cap">month</span></div>
				<div class="stat"><span class="stat-num">{stats.books_read.year}</span><span class="stat-cap">year</span></div>
				<div class="stat"><span class="stat-num">{stats.books_read.all_time}</span><span class="stat-cap">all-time</span></div>
			</div>
		</div>
	</div>
{/if}

{#if entries.length === 0}
	<p class="empty">No reading progress yet. Configure KOReader Sync on the X4.</p>
{:else}
	{#each grouped as group}
		<div class="date-group">
			<div class="date-heading">{group.heading}</div>
			{#each group.items as entry}
				<div class="log-entry">
					<div class="log-entry-title">
						{#if entry.title_resolved}
							<a href="/books/{encodeURIComponent(entry.title_resolved)}">{entry.title_resolved}</a>
						{:else}
							<span style="color:var(--text-muted);font-family:var(--font-mono);font-size:12px">{entry.document.slice(0, 16)}…</span>
							<a href="/aliases" style="font-size:11px;margin-left:.5rem;opacity:.7">→ map in aliases</a>
						{/if}
					</div>
					<div class="log-entry-detail">
						{#if entry.page}p{entry.page}{#if entry.total_pages} / ~{entry.total_pages}{/if}{:else}{entry.percentage_display}%{/if}
						{#if cfi(entry.progress)} · {cfi(entry.progress)}{/if}
						· {new Date(entry.at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
					</div>
				</div>
			{/each}
		</div>
	{/each}
{/if}

<style>
	.stats { display: flex; flex-wrap: wrap; gap: 1.75rem; margin-bottom: 1.5rem; padding-bottom: 1.25rem; border-bottom: 1px solid var(--border); }
	.stats-label { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .08em; color: var(--text-muted); margin-bottom: .4rem; }
	.stats-row { display: flex; gap: 1.1rem; }
	.stat { display: flex; flex-direction: column; }
	.stat-num { font-size: 20px; font-weight: 700; color: var(--text); line-height: 1.15; }
	.stat-cap { font-size: 10px; color: var(--text-muted); text-transform: uppercase; letter-spacing: .05em; }
</style>
