<script lang="ts">
	import { onMount } from 'svelte';

	const KEY = 'books-show-covers';

	let { show = $bindable(true) }: { show?: boolean } = $props();

	// Default on, and only the explicit string turns it off, so a cleared or
	// corrupt value falls back to showing covers rather than hiding them.
	onMount(() => {
		show = localStorage.getItem(KEY) !== 'false';
	});

	function toggle() {
		show = !show;
		localStorage.setItem(KEY, String(show));
	}
</script>

<button
	class="covers-toggle"
	onclick={toggle}
	aria-pressed={show}
	title={show ? 'Hide cover art' : 'Show cover art'}
>
	{show ? 'Hide covers' : 'Show covers'}
</button>

<style>
	.covers-toggle {
		font-size: 12px;
		padding: 0.2rem 0.55rem;
	}
	.covers-toggle[aria-pressed='true'] {
		border-color: var(--text-muted);
	}
</style>
