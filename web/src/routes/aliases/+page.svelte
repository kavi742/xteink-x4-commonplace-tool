<script lang="ts">
	import { api } from '$lib/api';
	import type { Alias, SyncDevice, UnresolvedAlias } from '$lib/api';

	let { data } = $props();
	let aliases = $state<Alias[]>(data.aliases);
	let unresolved = $state<UnresolvedAlias[]>(data.unresolved ?? []);
	let devices = $state<SyncDevice[]>(data.devices ?? []);
	let editing = $state<string | null>(null);
	let editValue = $state('');

	const scannable = $derived(
		new Set(devices.filter(d => d.scannable).map(d => d.device_id))
	);

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

{#if devices.length > 0}
	<div style="margin-bottom:1.5rem">
		<p class="section-label" style="margin-bottom:.5rem">Sync devices</p>
		<table class="alias-table">
			<thead><tr><th>Device</th><th>Books</th><th>Updates</th><th>Last sync</th><th>Titles</th></tr></thead>
			<tbody>
				{#each devices as d}
					<tr>
						<td>{d.device || d.device_id}</td>
						<td style="font-size:11px">{d.book_count}</td>
						<td style="font-size:11px">{d.update_count}</td>
						<td style="font-size:11px">{new Date(d.last_seen * 1000).toLocaleString()}</td>
						<td style="font-size:11px;color:var(--text-muted)">
							{d.scannable ? 'auto (File Transfer scan)' : 'KOReader metadata / manual'}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}

{#if unresolved.length > 0}
	<div style="background:var(--bg-sidebar);border:1px solid var(--border);border-radius:var(--radius);padding:.75rem;margin-bottom:1.5rem">
		<p class="section-label" style="margin-bottom:.5rem;color:#f5a97f">⚠ Unresolved ({unresolved.length})</p>
		<p style="font-size:12px;color:var(--text-muted);margin-bottom:.75rem">
			These hashes appear in your reading log but have no title mapping.
			Turn on <em>Send Metadata</em> on each device and they name their own books —
			X4: Settings → System → KOReader Sync; KOReader: Menu → Tools → Progress sync.
			For existing X4 hashes you can also run <code style="font-size:11px">sync_once</code> in File Transfer mode,
			or type a title below.
		</p>
		<table class="alias-table">
			<thead><tr><th>Hash</th><th>Device</th><th>Last seen</th><th>Progress</th><th>Title</th></tr></thead>
			<tbody>
				{#each unresolved as u}
					<tr>
						<td>{u.document.slice(0, 16)}…</td>
						<td style="font-size:11px" title={scannable.has(u.device_id) ? 'Auto-resolvable via sync_once' : 'Needs a manual title'}>
							{u.device || '—'}
						</td>
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
	<p class="empty">No aliases yet. Sync from a KOReader device to populate.</p>
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
							{#if alias.linked}
								<span class="linked" title="Shares a book with another hash — these devices sync with each other">⇄ linked</span>
							{/if}
						{/if}
					</td>
					<td style="font-size:11px;color:var(--text-muted)">{alias.resolved_by}</td>
				</tr>
			{/each}
		</tbody>
	</table>
{/if}

<style>
	.linked { margin-left: .4rem; font-size: 10px; color: #8bd5ca; white-space: nowrap; }
</style>
