<script lang="ts">
	import { api } from '$lib/api';

	let { slug }: { slug: string } = $props();

	let open = $state(false);
	let url = $state('');
	let busy = $state(false);
	let error = $state<string | null>(null);

	function toggle() {
		open = !open;
		error = null;
	}

	async function save() {
		if (!url.trim()) return;
		busy = true;
		error = null;
		try {
			await api.books.setCover(slug, url.trim());
			// The filename never changes, only its contents, so a reload is what
			// gets the browser to drop the old image.
			location.reload();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
			busy = false;
		}
	}

	async function reset() {
		busy = true;
		error = null;
		try {
			await api.books.clearCover(slug);
			location.reload();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
			busy = false;
		}
	}
</script>

<div class="cover-edit">
	<button class="cover-btn" onclick={toggle} aria-expanded={open}>Cover</button>

	{#if open}
		<div class="cover-panel">
			<label class="cover-label" for="cover-url">Direct image URL</label>
			<input
				id="cover-url"
				type="text"
				bind:value={url}
				placeholder="https://.../cover.jpg"
				disabled={busy}
			/>
			<p class="cover-hint">
				Must be an https link straight to the image. Links that redirect are refused.
			</p>
			{#if error}<p class="cover-error">{error}</p>{/if}
			<div class="cover-actions">
				<button onclick={save} disabled={busy || !url.trim()}>
					{busy ? 'Fetching…' : 'Use this cover'}
				</button>
				<button onclick={reset} disabled={busy}>Reset to automatic</button>
			</div>
		</div>
	{/if}
</div>

<style>
	.cover-edit { position: relative; }
	.cover-btn { font-size: 12px; padding: 0.2rem 0.55rem; }

	.cover-panel {
		position: absolute;
		right: 0;
		top: calc(100% + 0.35rem);
		z-index: 20;
		width: 22rem;
		max-width: 80vw;
		padding: 0.75rem;
		background: var(--bg-card);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		box-shadow: 0 4px 14px var(--shadow-color);
	}
	.cover-label {
		display: block;
		font-size: 10px;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.1em;
		color: var(--text-muted);
		margin-bottom: 0.4rem;
	}
	.cover-hint { font-size: 11px; color: var(--text-muted); margin-top: 0.4rem; }
	.cover-error { font-size: 12px; color: var(--danger); margin-top: 0.4rem; }
	.cover-actions { display: flex; gap: 0.4rem; margin-top: 0.6rem; flex-wrap: wrap; }
	.cover-actions button { font-size: 12px; }
</style>
