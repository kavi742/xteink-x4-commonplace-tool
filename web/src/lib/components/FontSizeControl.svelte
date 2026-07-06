<script lang="ts">
	import { onMount } from 'svelte';

	const STEPS = [0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4, 1.55, 1.7];
	const DEFAULT = 1.1;
	const KEY = 'app-font-zoom';

	let zoom = $state(DEFAULT);
	let open = $state(false);

	onMount(() => {
		const saved = localStorage.getItem(KEY);
		zoom = saved ? parseFloat(saved) : DEFAULT;
		apply(zoom);
	});

	function apply(z: number) {
		document.documentElement.style.zoom = String(z);
	}

	function set(z: number) {
		zoom = z;
		apply(z);
		localStorage.setItem(KEY, String(z));
	}

	function smaller() {
		const i = STEPS.indexOf(zoom);
		if (i > 0) set(STEPS[i - 1]);
	}

	function larger() {
		const i = STEPS.indexOf(zoom);
		if (i < STEPS.length - 1) set(STEPS[i + 1]);
	}

	const idx = $derived(STEPS.indexOf(zoom));
</script>

<div class="font-ctrl" class:open>
	{#if open}
		<button class="font-btn" onclick={smaller} disabled={idx <= 0} aria-label="Smaller text">A−</button>
		<span class="font-label">{Math.round(zoom * 100)}%</span>
		<button class="font-btn" onclick={larger} disabled={idx >= STEPS.length - 1} aria-label="Larger text">A+</button>
	{/if}
	<button class="font-toggle" onclick={() => open = !open} aria-label="Font size">Aa</button>
</div>

<style>
	.font-ctrl {
		display: none; /* shown only on mobile via app.css media query */
	}
</style>
