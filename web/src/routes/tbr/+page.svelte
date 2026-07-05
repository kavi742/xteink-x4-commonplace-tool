<script lang="ts">
	import { api } from '$lib/api';
	import TbrRow from '$lib/components/TbrRow.svelte';
	import type { TbrBook } from '$lib/api';

	let { data } = $props();
	let books = $state<TbrBook[]>(data.books);

	let adding = $state(false);
	let newTitle = $state('');
	let newAuthor = $state('');
	let newUrl = $state('');
	let newNotes = $state('');
	let showDone = $state(false);

	let queued  = $derived(books.filter(b => b.status === 'queued'));
	let reading = $derived(books.filter(b => b.status === 'reading'));
	let done    = $derived(books.filter(b => b.status === 'done'));

	const STATUS_CYCLE: Record<string, TbrBook['status']> = {
		queued: 'reading', reading: 'done', done: 'queued',
	};

	async function add() {
		if (!newTitle.trim()) return;
		const b = await api.tbr.add({
			title: newTitle.trim(),
			author: newAuthor.trim(),
			source_url: newUrl.trim(),
			notes: newNotes.trim(),
		});
		books = [b, ...books];
		newTitle = newAuthor = newUrl = newNotes = '';
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
	<button onclick={() => adding = !adding} style="font-size:12px">
		{adding ? '✕ Cancel' : '+ Add book'}
	</button>
</div>

{#if adding}
	<div class="tbr-form">
		<input type="text" bind:value={newTitle} placeholder="Title *" autofocus
			onkeydown={(e) => { if (e.key === 'Enter') add(); if (e.key === 'Escape') adding = false; }} />
		<div style="display:flex;gap:.5rem">
			<input type="text" bind:value={newAuthor} placeholder="Author" style="flex:1" />
			<input type="text" bind:value={newUrl} placeholder="URL (optional)" style="flex:1" />
		</div>
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
</style>

