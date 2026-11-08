<script lang="ts">
	import DateGroup from '$lib/components/DateGroup.svelte';
	import ClippingCard from '$lib/components/ClippingCard.svelte';
	import { api, type Article, type ArticleDetail, type Screenshot } from '$lib/api';

	type Section =
		| { status: 'loading' }
		| { status: 'error'; message: string }
		| { status: 'ready'; detail: ArticleDetail };

	let { data } = $props();
	let { articles } = $derived(data);

	let open = $state<Record<string, boolean>>({});
	let sections = $state<Record<string, Section>>({});

	async function fetchDetail(slug: string) {
		sections[slug] = { status: 'loading' };
		try {
			sections[slug] = { status: 'ready', detail: await api.articles.get(slug) };
		} catch (e) {
			sections[slug] = {
				status: 'error',
				message: e instanceof Error ? e.message : 'Could not load this article.',
			};
		}
	}

	function toggle(slug: string) {
		open[slug] = !open[slug];
		if (open[slug] && !sections[slug]) fetchDetail(slug);
	}

	function plural(n: number, word: string) {
		return `${n} ${word}${n === 1 ? '' : 's'}`;
	}

	function metaLine(a: Article): string {
		const bits: string[] = [];
		if (a.page !== null) bits.push(a.total_pages ? `p${a.page} / ~${a.total_pages}` : `p${a.page}`);
		if (a.percentage_display !== null) bits.push(`${a.percentage_display}%`);
		if (a.last_read_at) bits.push(new Date(a.last_read_at * 1000).toLocaleDateString());
		bits.push(plural(a.screenshot_count, 'screenshot'));
		bits.push(plural(a.clipping_count, 'clipping'));
		return bits.join(' · ');
	}

	function byDate(screenshots: Screenshot[]): [string, Screenshot[]][] {
		const map = new Map<string, Screenshot[]>();
		for (const s of screenshots) {
			const d = s.sync_date || 'Unknown';
			if (!map.has(d)) map.set(d, []);
			map.get(d)!.push(s);
		}
		return [...map.entries()].sort((a, b) => b[0].localeCompare(a[0]));
	}
</script>

<svelte:head><title>Articles - xteink</title></svelte:head>

<div class="articles-head">
	<h1 class="page-title">Articles</h1>
	<span class="articles-count">{articles.length}</span>
</div>

{#if articles.length === 0}
	<p class="empty">
		No articles yet. Send an Instapaper article to the reader, then sync reading progress
		or press File Transfer on the X4 to archive screenshots.
	</p>
{:else}
	<div class="article-list">
		{#each articles as article (article.slug)}
			{@const section = sections[article.slug]}
			{@const expanded = !!open[article.slug]}
			<section class="article">
				<button
					class="article-head"
					aria-expanded={expanded}
					aria-controls="article-body-{article.slug}"
					onclick={() => toggle(article.slug)}
				>
					<span class="article-chevron" aria-hidden="true">{expanded ? '▾' : '▸'}</span>
					<span class="article-headings">
						<span class="article-title">
							{article.title}
							{#if article.finished}<span class="article-done">finished</span>{/if}
						</span>
						<span class="article-meta">{metaLine(article)}</span>
					</span>
				</button>

				{#if expanded}
					<div class="article-body" id="article-body-{article.slug}">
						{#if !section || section.status === 'loading'}
							<p class="article-state">Loading…</p>
						{:else if section.status === 'error'}
							<p class="article-state">
								{section.message}
								<button class="article-retry" onclick={() => fetchDetail(article.slug)}>Retry</button>
							</p>
						{:else}
							{@const detail = section.detail}
							<div class="group">
								<p class="group-label">
									Screenshots <span class="group-n">{detail.screenshots.length}</span>
								</p>
								{#if detail.screenshots.length === 0}
									<p class="group-empty">No screenshots for this article.</p>
								{:else}
									{#each byDate(detail.screenshots) as [date, shots]}
										<DateGroup {date} screenshots={shots} siblings={detail.screenshots.map(s => s.id)} />
									{/each}
								{/if}
							</div>

							<div class="group">
								<p class="group-label">
									Clippings <span class="group-n">{detail.clippings.length}</span>
								</p>
								{#if detail.clippings.length === 0}
									<p class="group-empty">No clippings for this article.</p>
								{:else}
									<div class="clip-list">
										{#each detail.clippings as c (c.id)}
											<ClippingCard clipping={c} />
										{/each}
									</div>
								{/if}
							</div>
						{/if}
					</div>
				{/if}
			</section>
		{/each}
	</div>
{/if}

<style>
	.articles-head { display: flex; align-items: baseline; gap: .75rem; }
	.articles-count { font-size: 12px; color: var(--text-muted); }

	.article-list { display: flex; flex-direction: column; gap: .5rem; }
	.article {
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--bg-card);
		overflow: hidden;
	}
	.article-head {
		display: flex;
		align-items: flex-start;
		gap: .5rem;
		width: 100%;
		text-align: left;
		background: none;
		border: none;
		border-radius: 0;
		padding: .7rem .9rem;
	}
	.article-head:hover { background: var(--active-bg); }
	.article-chevron { font-size: 11px; color: var(--text-muted); line-height: 1.5; }
	.article-headings { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
	.article-title { font-size: 13px; font-weight: 600; line-height: 1.35; }
	.article-meta { font-size: 11px; color: var(--text-muted); }
	.article-done {
		display: inline-block;
		margin-left: .35rem;
		padding: 0 .3rem;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		font-size: 9px;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: .06em;
		color: var(--text-muted);
		vertical-align: middle;
	}

	.article-body {
		border-top: 1px solid var(--border);
		padding: .9rem;
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}
	.article-state { font-size: 12px; color: var(--text-muted); }
	.article-retry { font-size: 12px; margin-left: .5rem; }

	.group-label {
		font-size: 10px;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: .1em;
		color: var(--text-muted);
		border-bottom: 1px solid var(--border);
		padding-bottom: .4rem;
		margin-bottom: .6rem;
	}
	.group-n { font-size: 10px; font-weight: 400; margin-left: .15rem; }
	.group-empty { font-size: 12px; color: var(--text-muted); }
	.clip-list { display: flex; flex-direction: column; gap: .5rem; }

	.group :global(.date-group:last-child) { margin-bottom: 0; }

	@media (max-width: 768px) {
		.article-title { font-size: 15px; }
		.article-meta { font-size: 12px; }
		.article-body { padding: .75rem .6rem; }
	}
</style>
