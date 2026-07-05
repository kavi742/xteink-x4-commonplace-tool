<script lang="ts">
	import { api } from '$lib/api';
	import TbrRow from '$lib/components/TbrRow.svelte';
	import type { TbrBook, OlBook } from '$lib/api';

	let { data } = $props();
	let books = $state<TbrBook[]>(data.books);

	let adding = $state(false);
	let searchQ = $state('');
	let searchResults = $state<OlBook[]>([]);
	let searching = $state(false);
	let newTitle = $state('');
	let newAuthor = $state('');
	let newUrl = $state('');
	let newNotes = $state('');
	let showDone = $state(false);
	let searchTimer: ReturnType<typeof setTimeout>;

	let queued  = $derived(books.filter(b => b.status === 'queued'));
	let reading = $derived(books.filter(b => b.status === 'reading'));
	let done    = $derived(books.filter(b => b.status === 'done'));

	const STATUS_CYCLE: Record<string, TbrBook['status']> = {
		queued: 'reading', reading: 'done', done: 'queued',
	};

	function onSearchInput() {
		clearTimeout(searchTimer);
		if (!searchQ.trim()) { searchResults = []; return; }
		searchTimer = setTimeout(async () => {
			searching = true;
			searchResults = await api.tbr.search(searchQ).catch(() => []);
			searching = false;
		}, 400);
	}

	function selectResult(book: OlBook) {
		newTitle = book.title;
		newAuthor = book.author;
		newUrl = book.ol_key ? `https://openlibrary.org${book.ol_key}` : '';
		searchQ = '';
		searchResults = [];
	}

	async function add() {
		if (!newTitle.trim()) return;
		const b = await api.tbr.add({
			title: newTitle.trim(),
			author: newAuthor.trim(),
			source_url: newUrl.trim(),
			notes: newNotes.trim(),
		});
		books = [b, ...books];
		newTitle = newAuthor = newUrl = newNotes = searchQ = '';
		searchResults = [];
		adding = false;
	}

	async function cycle(book: TbrBook) {
		const next = STATUS_CYCLE[book.status] ?? 'queued';
		await api.tbr.update(book.id, { status: next });
		books = books.map(b => b.id === book.id ? { ...b, status: next } : b);
	}

	async function remove(id: number) {
		await api.tbr.delete(id);
		books = books.filter(b => b.id !== id);
	}
</script>

<svelte:head><title>TBR — xteink</title></svelte:head>

<div style="display:flex;align-items:baseline;justify-content:space-between;margin-bottom:1.25rem">
	<h1 class="page-title" style="margin-bottom:0">To Be Read</h1>
	<button onclick={() => { adding = !adding; searchQ = ''; searchResults = []; }} style="font-size:12px">
		{adding ? '✕ Cancel' : '+ Add book'}
	</button>
</div>

{#if adding}
	<div class="tbr-form">
		<!-- Book search -->
		<div style="position:relative">
			<input type="text" bind:value={searchQ} oninput={onSearchInput}
				placeholder="Search Open Library…" />
			{#if searching}
				<span style="position:absolute;right:.5rem;top:.4rem;font-size:11px;color:var(--text-muted)">searching…</span>
			{/if}
			{#if searchResults.length > 0}
				<ul class="ol-results">
					{#each searchResults as r}
						<li>
							<button onclick={() => selectResult(r)} class="ol-result-btn">
								{#if r.cover_url}
									<img src={r.cover_url} alt="" class="ol-cover" />
								{/if}
								<div>
									<div style="font-size:12px;font-weight:600">{r.title}</div>
									{#if r.author}<div style="font-size:11px;color:var(--text-muted)">{r.author}{r.year ? ` · ${r.year}` : ''}</div>{/if}
								</div>
							</button>
						</li>
					{/each}
				</ul>
			{/if}
		</div>

		<!-- Manual fields -->
		<div style="display:flex;gap:.5rem">
			<input type="text" bind:value={newTitle} placeholder="Title *" style="flex:2"
				onkeydown={(e) => { if (e.key === 'Enter') add(); if (e.key === 'Escape') adding = false; }} />
			<input type="text" bind:value={newAuthor} placeholder="Author" style="flex:1" />
		</div>
		<input type="text" bind:value={newUrl} placeholder="URL (optional)" />
		<textarea rows="2" bind:value={newNotes} placeholder="Notes…"></textarea>
		<div style="display:flex;gap:.5rem;justify-content:flex-end">
			<button onclick={() => adding = false} style="color:var(--text-muted)">Cancel</button>
			<button onclick={add} disabled={!newTitle.trim()}>Add</button>
		</div>
	</div>
{/if}

{#if reading.length > 0}
	<p class="section-label">Currently reading</p>
	{#each reading as book (book.id)}
		<TbrRow {book} oncycle={() => cycle(book)} onremove={() => remove(book.id)} />
	{/each}
{/if}

{#if queued.length > 0}
	{#if reading.length > 0}<div style="height:.75rem"></div>{/if}
	<p class="section-label">Up next</p>
	{#each queued as book (book.id)}
		<TbrRow {book} oncycle={() => cycle(book)} onremove={() => remove(book.id)} />
	{/each}
{/if}

{#if queued.length === 0 && reading.length === 0 && done.length === 0}
	<p class="empty">Your TBR list is empty. Add a book to get started.</p>
{/if}

{#if done.length > 0}
	<div style="height:.75rem"></div>
	<button
		onclick={() => showDone = !showDone}
		style="border:none;background:none;font-size:12px;color:var(--text-muted);padding:.2rem 0;cursor:pointer"
	>
		{showDone ? '▾' : '▸'} Done ({done.length})
	</button>
	{#if showDone}
		<div style="margin-top:.25rem">
			{#each done as book (book.id)}
				<TbrRow {book} oncycle={() => cycle(book)} onremove={() => remove(book.id)} />
			{/each}
		</div>
	{/if}
{/if}

<style>
	.tbr-form {
		display: flex;
		flex-direction: column;
		gap: .5rem;
		background: var(--bg-card);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: .75rem;
		margin-bottom: 1.25rem;
	}
	.ol-results {
		position: absolute;
		top: 100%;
		left: 0;
		right: 0;
		z-index: 50;
		background: var(--bg-card);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		box-shadow: 0 4px 12px rgba(0,0,0,.1);
		list-style: none;
		max-height: 280px;
		overflow-y: auto;
		margin-top: 2px;
	}
	.ol-result-btn {
		display: flex;
		align-items: center;
		gap: .5rem;
		width: 100%;
		text-align: left;
		border: none;
		background: none;
		padding: .5rem .75rem;
		cursor: pointer;
	}
	.ol-result-btn:hover { background: var(--active-bg); }
	.ol-cover { width: 28px; height: 40px; object-fit: cover; border-radius: 2px; flex: 0 0 auto; }
</style>

