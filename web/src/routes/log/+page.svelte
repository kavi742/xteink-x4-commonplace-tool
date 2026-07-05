<script lang="ts">
	let { data } = $props();
	let { entries } = $derived(data);

	function cfi(progress: string) {
		const m = progress?.match(/DocFragment\[(\d+)\]/);
		return m ? `§${m[1]}` : '';
	}
</script>

<h1 class="page-title">Reading Log</h1>

{#if entries.length === 0}
	<p class="empty">No reading progress yet. Configure KOReader Sync on the X4.</p>
{:else}
	{#each entries as entry}
		<div class="log-entry">
			<div class="log-entry-title">
				{entry.title_resolved ?? entry.document.slice(0, 12) + '…'}
			</div>
			<div class="log-entry-detail">
				{entry.percentage_display}%
				{#if cfi(entry.progress)} · {cfi(entry.progress)}{/if}
				· {new Date(entry.at).toLocaleString()}
			</div>
		</div>
	{/each}
{/if}
