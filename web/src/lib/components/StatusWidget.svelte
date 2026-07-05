<script lang="ts">
	import { api } from '$lib/api';
	import { onMount } from 'svelte';

	let total = $state(0);
	let todayCount = $state(0);
	let lastSync = $state<string | null>(null);

	onMount(async () => {
		try {
			const s = await api.status();
			total = s.screenshots.total;
			todayCount = s.screenshots.today_count;
			lastSync = s.screenshots.last_sync_at;
		} catch {}
	});
</script>

{#if total > 0}
	<div class="status-widget">
		<strong>{total}</strong> screenshots
		{#if todayCount > 0} · <strong>{todayCount}</strong> today{/if}
		{#if lastSync}<br>Last: {new Date(lastSync).toLocaleDateString()}{/if}
	</div>
{/if}
