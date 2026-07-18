<script lang="ts">
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	// replaceState so the initial `/` entry is swapped for `/books` rather than
	// pushed — otherwise hardware Back at the home screen bounces `/` -> `/books`
	// forever and the app can never be exited via Back.
	onMount(() => goto('/books', { replaceState: true }));
</script>

