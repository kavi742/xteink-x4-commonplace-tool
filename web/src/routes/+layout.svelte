<script lang="ts">
	import '../styles/app.css';
	import { page } from '$app/state';
	import { panel } from '$lib/panel.svelte';
	import ScreenshotPanel from '$lib/components/ScreenshotPanel.svelte';
	import StatusWidget from '$lib/components/StatusWidget.svelte';

	let { children, data } = $props();
	let { books } = $derived(data);

	const navLinks = [
		{ href: '/books',   label: 'Books' },
		{ href: '/log',     label: 'Reading Log' },
		{ href: '/aliases', label: 'Aliases' },
	];

	function isActive(href: string) {
		return page.url.pathname === href || page.url.pathname.startsWith(href + '/');
	}
</script>

<div class="app">
	<aside class="sidebar">
		<div class="sidebar-top">
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

