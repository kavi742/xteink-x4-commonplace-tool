<script lang="ts">
	let { data } = $props();
	let { entries } = $derived(data);

	function cfi(progress: string) {
		const m = progress?.match(/DocFragment\[(\d+)\]/);
		return m ? `\u00a7${m[1]}` : '';
	}
</script>

<svelte:head><title>Reading Log — xteink</title></svelte:head>

<h1 class="page-title">Reading Log</h1>

{#if entries.length === 0}
	<p class="empty">No reading progress yet. Configure KOReader Sync on the X4.</p>
{:else}
	{#each entries as entry}
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
				{entry.percentage_display}%
				{#if cfi(entry.progress)} · {cfi(entry.progress)}{/if}
				· {new Date(entry.at).toLocaleDateString()}
			</div>
		</div>
	{/each}
{/if}
