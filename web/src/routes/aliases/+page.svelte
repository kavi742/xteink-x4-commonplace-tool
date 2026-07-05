<script lang="ts">
	import { api } from '$lib/api';
	import type { Alias } from '$lib/api';

	let { data } = $props();
	let aliases = $state<Alias[]>(data.aliases);
	let unresolved = $state(data.unresolved ?? []);
	let editing = $state<string | null>(null);
	let editValue = $state('');

	function startEdit(hash: string, current: string) {
		editing = hash;
		editValue = current;
	}

	async function saveEdit(hash: string) {
		if (!editValue.trim()) return;
		await api.aliases.set(hash, editValue.trim());
		aliases = aliases.map(a => a.hash === hash ? { ...a, title: editValue.trim() } : a);
		// Remove from unresolved if it was there
		unresolved = unresolved.filter(u => u.document !== hash);
		editing = null;
	}

	function keydown(e: KeyboardEvent, hash: string) {
		if (e.key === 'Enter') saveEdit(hash);
		if (e.key === 'Escape') editing = null;
	}
</script>

<svelte:head><title>Aliases — xteink</title></svelte:head>

<h1 class="page-title">Aliases</h1>
<p style="font-size:12px;color:var(--text-muted);margin-bottom:1rem">
	Hash → title mappings for books in the reading log. Click a title to rename.
</p>

{#if unresolved.length > 0}
	<div style="background:var(--bg-sidebar);border:1px solid var(--border);border-radius:var(--radius);padding:.75rem;margin-bottom:1.5rem">
		<p class="section-label" style="margin-bottom:.5rem;color:#b45309">⚠ Unresolved ({unresolved.length})</p>
		<p style="font-size:12px;color:var(--text-muted);margin-bottom:.75rem">
			These hashes appear in your reading log but have no title mapping.
			Put the X4 in File Transfer mode and run <code style="font-size:11px">sync_once</code> to auto-resolve,
			or type a title below.
		</p>
		<table class="alias-table">
			<thead><tr><th>Hash</th><th>Last seen</th><th>Progress</th><th>Title</th></tr></thead>
			<tbody>
				{#each unresolved as u}
					<tr>
						<td>{u.document.slice(0, 16)}…</td>
						<td style="font-size:11px">{new Date(u.last_seen * 1000).toLocaleDateString()}</td>
						<td style="font-size:11px">{u.percentage_display}%</td>
						<td>
							{#if editing === u.document}
								<input type="text" bind:value={editValue}
									onblur={() => saveEdit(u.document)}
									onkeydown={(e) => keydown(e, u.document)}
									style="max-width:24ch" autofocus />
							{:else}
								<span onclick={() => startEdit(u.document, '')} style="cursor:text;color:var(--text-muted);font-style:italic" title="Click to map">
									click to map →
								</span>
							{/if}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}

{#if aliases.length === 0}
	<p class="empty">No aliases yet. Sync from the X4 to populate.</p>
{:else}
	<table class="alias-table">
		<thead>
			<tr><th>Hash</th><th>Filename</th><th>Title</th><th>Source</th></tr>
		</thead>
		<tbody>
			{#each aliases as alias}
				<tr>
					<td>{alias.hash.slice(0, 12)}…</td>
					<td style="font-size:12px">{alias.filename || '—'}</td>
					<td>
						{#if editing === alias.hash}
							<input
								type="text"
								bind:value={editValue}
								onblur={() => saveEdit(alias.hash)}
								onkeydown={(e) => keydown(e, alias.hash)}
								style="max-width:24ch"
							/>
						{:else}
							<span onclick={() => startEdit(alias.hash, alias.title)} style="cursor:text" title="Click to edit"
								>{alias.title}</span>
						{/if}
					</td>
					<td style="font-size:11px;color:var(--text-muted)">{alias.resolved_by}</td>
				</tr>
			{/each}
		</tbody>
	</table>
{/if}
