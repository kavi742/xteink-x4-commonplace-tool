<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { isAndroidApp, relayStatus, relaySetUpstream, type RelayStatus } from '$lib/relay';

	let status = $state<RelayStatus | null>(null);
	let host = $state('');
	let open = $state(false);
	let timer: ReturnType<typeof setInterval> | undefined;

	async function refresh() {
		status = await relayStatus();
		if (!open) host = status?.host ?? '';
	}

	onMount(() => {
		if (!isAndroidApp()) return;
		refresh();
		timer = setInterval(refresh, 4000);
	});
	onDestroy(() => {
		if (timer) clearInterval(timer);
	});

	async function save() {
		if (host.trim()) status = await relaySetUpstream(host.trim(), status?.port ?? 8090);
		open = false;
	}
</script>

{#if isAndroidApp()}
	<div class="relaybar">
		<button class="relaybar-main" onclick={() => (open = !open)}>
			<span class="dot" class:on={status?.running}></span>
			<span>Relay {status?.running ? 'on' : 'off'}</span>
			{#if status?.connections}<span>· {status.connections} active</span>{/if}
			<span class="host">→ {status?.host}:{status?.port}</span>
		</button>
		{#if open}
			<div class="relaybar-cfg">
				<label for="relay-host">Homelab IP</label>
				<input id="relay-host" type="text" bind:value={host} placeholder="100.114.210.37" />
				<button onclick={save}>Save</button>
			</div>
			<p class="relaybar-hint">
				Point the X4's KOReader sync at
				<code>http://{status?.hotspotIp ?? "<phone hotspot IP>"}:{status?.listenPort ?? 8090}</code>.
				Keep this app open while syncing.
			</p>
		{/if}
	</div>
{/if}

<style>
	.relaybar { border: 1px solid var(--border); border-radius: var(--radius); margin-bottom: 1rem; font-size: 12px; background: var(--bg-card); }
	.relaybar-main { display: flex; align-items: center; gap: .4rem; width: 100%; text-align: left; border: none; background: none; color: var(--text-muted); padding: .5rem .6rem; }
	.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--text-muted); opacity: .4; flex: none; }
	.dot.on { background: #a6da95; opacity: 1; }
	.host { margin-left: auto; font-family: var(--font-mono); font-size: 11px; }
	.relaybar-cfg { display: flex; gap: .4rem; align-items: center; padding: 0 .6rem .5rem; }
	.relaybar-cfg label { font-size: 11px; color: var(--text-muted); white-space: nowrap; }
	.relaybar-cfg input { flex: 1; }
	.relaybar-hint { padding: 0 .6rem .5rem; color: var(--text-muted); font-size: 11px; line-height: 1.4; }
	.relaybar-hint code { font-family: var(--font-mono); font-size: 10px; }
</style>
