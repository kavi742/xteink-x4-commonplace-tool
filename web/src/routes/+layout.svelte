<script lang="ts">
	import '../styles/app.css';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { onMount } from 'svelte';
	import { panel } from '$lib/panel.svelte';
	import { api } from '$lib/api';
	import type { HighlightWithMeta } from '$lib/api';
	import ScreenshotPanel from '$lib/components/ScreenshotPanel.svelte';
	import StatusWidget from '$lib/components/StatusWidget.svelte';

	let { children, data } = $props();
	let { books } = $derived(data);
	let recentHighlights = $state<HighlightWithMeta[]>([]);

	let notedCount = $state(0);

	onMount(async () => {
		recentHighlights = await api.highlights.listAll(5).catch(() => []);
		try {
			const s = await api.status();
			notedCount = (s as any).screenshots?.noted_count ?? 0;
		} catch {}
	});

	const navLinks = [
		{ href: '/books',      label: 'Books' },
		{ href: '/log',        label: 'Reading Log' },
		{ href: '/tbr',        label: 'TBR' },
		{ href: '/highlights', label: 'Highlights' },
		{ href: '/aliases',    label: 'Aliases' },
	];

	let searchQ = $state('');

	function submitSearch(e: Event) {
		e.preventDefault();
		if (searchQ.trim()) goto(`/search?q=${encodeURIComponent(searchQ.trim())}`, { invalidateAll: true });
	}

	function isActive(href: string) {
		return page.url.pathname === href || page.url.pathname.startsWith(href + '/');
	}
</script>

<div class="app">
	<aside class="sidebar">
		<div class="sidebar-top">
			<a href="/books" class="sidebar-home" title="Home">
				<span class="sidebar-home-name">xteink</span>
				<span class="sidebar-home-sub">commonplace</span>
			</a>
			<form onsubmit={submitSearch} style="display:flex;gap:.35rem;margin-bottom:.6rem">
				<input
					type="text"
					bind:value={searchQ}
					placeholder="Search…"
					style="flex:1;font-size:12px;padding:.3rem .5rem"
				/>
				<button type="submit" style="font-size:12px;padding:.3rem .5rem">↵</button>
			</form>
			<nav>
				<ul class="nav-links">
					{#each navLinks as link}
						<li><a href={link.href} class:active={isActive(link.href)}>{link.label}</a></li>
					{/each}
				</ul>
			</nav>
			<StatusWidget />
		</div>
		<div class="sidebar-scroll">
			<p class="section-label">Books</p>
			<ul class="book-index">
				{#each books as book}
					<li>
						<a
							href="/books/{encodeURIComponent(book.book_title)}"
							class:active={page.url.pathname === '/books/' + encodeURIComponent(book.book_title)}
						>
							<span>{book.book_title}</span>
							<span class="count">{book.screenshot_count}</span>
						</a>
					</li>
				{/each}
			</ul>

			{#if recentHighlights.length > 0}
				<p class="section-label" style="margin-top:1rem">Recent highlights</p>
				<div style="display:flex;flex-direction:column;gap:4px">
					{#each recentHighlights as h}
						<button
							class="sidebar-highlight"
							onclick={() => panel.open(h.screenshot_id, [])}
							title={h.book_title}
						>
							<mark class="sidebar-mark">{h.selected_text.slice(0, 60)}{h.selected_text.length > 60 ? '…' : ''}</mark>
							<span class="sidebar-hl-book">{h.book_title}</span>
						</button>
					{/each}
					<a href="/highlights" style="font-size:11px;color:var(--text-muted);padding:.2rem .5rem">All highlights →</a>
				</div>
			{/if}

			{#if notedCount > 0}
				<a
					href="/search?notes_only=1"
					class:active={page.url.pathname === '/search' && page.url.searchParams.get('notes_only') === '1'}
					style="display:flex;align-items:center;justify-content:space-between;padding:.2rem .5rem;border-radius:var(--radius);font-size:12px;color:var(--text-muted);margin-top:.75rem"
				>
					<span>📝 With notes</span>
					<span class="count">{notedCount}</span>
				</a>
			{/if}
		</div>
	</aside>

	<main class="main">
		{@render children()}
	</main>
</div>

{#if panel.id !== null}
	<div class="panel-overlay">
		<ScreenshotPanel />
	</div>
{/if}

