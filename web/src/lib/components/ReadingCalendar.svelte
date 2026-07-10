<script lang="ts">
	import type { ReadingCalendarDay } from '$lib/api';

	let { days = [] }: { days?: ReadingCalendarDay[] } = $props();

	let byDate = $derived(
		new Map(days.map((d) => [d.date, d] as [string, ReadingCalendarDay]))
	);
	let maxRead = $derived(Math.max(1, ...days.map((d) => d.percent_read)));
	let daysRead = $derived(days.length);
	let reached = $derived(days.reduce((n, d) => Math.max(n, d.end_pct), 0));

	const MONTHS = ['January','February','March','April','May','June','July','August','September','October','November','December'];
	const DOW = ['S', 'M', 'T', 'W', 'T', 'F', 'S'];

	type MonthGrid = { label: string; weeks: (string | null)[][] };

	let months = $derived.by(() => {
		const out: MonthGrid[] = [];
		if (days.length === 0) return out;
		const sorted = days.map((d) => d.date).slice().sort();
		const [fy, fm] = sorted[0].split('-').map(Number);
		const [ly, lm] = sorted[sorted.length - 1].split('-').map(Number);
		let y = fy, m = fm; // month is 1-based
		while (y < ly || (y === ly && m <= lm)) {
			out.push(buildMonth(y, m));
			m++;
			if (m > 12) { m = 1; y++; }
		}
		return out;
	});

	function buildMonth(year: number, month1: number): MonthGrid {
		const month = month1 - 1; // 0-based for Date
		const startDow = new Date(year, month, 1).getDay();
		const daysInMonth = new Date(year, month + 1, 0).getDate();
		const cells: (string | null)[] = [];
		for (let i = 0; i < startDow; i++) cells.push(null);
		for (let d = 1; d <= daysInMonth; d++) {
			cells.push(`${year}-${String(month1).padStart(2, '0')}-${String(d).padStart(2, '0')}`);
		}
		while (cells.length % 7 !== 0) cells.push(null);
		const weeks: (string | null)[][] = [];
		for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));
		return { label: `${MONTHS[month]} ${year}`, weeks };
	}

	function level(iso: string): number {
		const d = byDate.get(iso);
		if (!d) return 0;
		if (d.percent_read <= 0) return 1; // read that day, but no measurable delta
		const r = d.percent_read / maxRead;
		if (r < 0.25) return 1;
		if (r < 0.5) return 2;
		if (r < 0.75) return 3;
		return 4;
	}

	function tip(iso: string): string {
		const label = new Date(iso + 'T00:00').toLocaleDateString(undefined, {
			weekday: 'short', month: 'short', day: 'numeric'
		});
		const d = byDate.get(iso);
		if (!d) return label;
		const amt = d.percent_read > 0 ? `read ${d.percent_read}%` : 'opened';
		return `${label} · ${amt} · ${d.start_pct}% → ${d.end_pct}% · ${d.sessions} sync${d.sessions === 1 ? '' : 's'}`;
	}
</script>

{#if days.length > 0}
	<div class="cal">
		<div class="cal-summary">
			<span><strong>{daysRead}</strong> day{daysRead === 1 ? '' : 's'} read</span>
			<span><strong>{reached}%</strong> reached</span>
		</div>
		<div class="cal-months">
			{#each months as mo}
				<div class="cal-month">
					<div class="cal-month-label">{mo.label}</div>
					<div class="cal-dow">
						{#each DOW as d}<span>{d}</span>{/each}
					</div>
					{#each mo.weeks as week}
						<div class="cal-week">
							{#each week as iso}
								{#if iso}
									<div class="cal-cell l{level(iso)}" title={tip(iso)} aria-label={tip(iso)}></div>
								{:else}
									<div class="cal-cell blank"></div>
								{/if}
							{/each}
						</div>
					{/each}
				</div>
			{/each}
		</div>
		<div class="cal-legend">
			<span>Less</span>
			<span class="cal-cell l0"></span>
			<span class="cal-cell l1"></span>
			<span class="cal-cell l2"></span>
			<span class="cal-cell l3"></span>
			<span class="cal-cell l4"></span>
			<span>More</span>
		</div>
	</div>
{/if}

<style>
	.cal { margin-bottom: 1.5rem; }
	.cal-summary { display: flex; gap: 1rem; font-size: 12px; color: var(--text-muted); margin-bottom: .6rem; }
	.cal-summary strong { color: var(--text); }
	.cal-months { display: flex; flex-wrap: wrap; gap: 1.5rem; }
	.cal-month-label { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .06em; color: var(--text-muted); margin-bottom: .35rem; }
	.cal-dow, .cal-week { display: grid; grid-template-columns: repeat(7, 14px); gap: 3px; }
	.cal-dow { margin-bottom: 3px; }
	.cal-dow span { font-size: 9px; color: var(--text-muted); text-align: center; }
	.cal-week { margin-bottom: 3px; }
	.cal-cell { width: 14px; height: 14px; border-radius: 2px; background: var(--bg-sidebar); border: 1px solid var(--border); }
	.cal-cell.blank { background: transparent; border-color: transparent; }
	.cal-cell.l0 { background: var(--bg-sidebar); }
	.cal-cell.l1 { background: rgba(179, 129, 79, .30); border-color: transparent; }
	.cal-cell.l2 { background: rgba(179, 129, 79, .55); border-color: transparent; }
	.cal-cell.l3 { background: rgba(179, 129, 79, .78); border-color: transparent; }
	.cal-cell.l4 { background: rgba(179, 129, 79, 1); border-color: transparent; }
	.cal-legend { display: flex; align-items: center; gap: 3px; font-size: 10px; color: var(--text-muted); margin-top: .6rem; }
	.cal-legend .cal-cell { width: 11px; height: 11px; }
</style>
