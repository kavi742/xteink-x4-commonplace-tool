<script lang="ts">
	import { api } from '$lib/api';
	import type { ExportFormat, ExportInclude } from '$lib/api';

	let {
		slug,
		defaultInclude = ['highlights', 'clippings', 'notes', 'writing'],
		label = 'Export',
	}: {
		slug: string;
		defaultInclude?: ExportInclude[];
		label?: string;
	} = $props();

	const ALL: { value: ExportInclude; label: string }[] = [
		{ value: 'highlights', label: 'Highlights' },
		{ value: 'clippings', label: 'Clippings' },
		{ value: 'notes', label: 'Notes' },
		{ value: 'writing', label: 'Writing' },
	];

	const FORMATS: { value: ExportFormat; label: string }[] = [
		{ value: 'md', label: 'Markdown' },
		{ value: 'csv', label: 'CSV' },
		{ value: 'json', label: 'JSON' },
	];

	let open = $state(false);
	// Null until the user touches a checkbox, so a changed `defaultInclude`
	// still reaches an untouched menu.
	let overrides = $state<ExportInclude[] | null>(null);
	let copied = $state('');
	let root = $state<HTMLElement | null>(null);

	let selected = $derived(overrides ?? defaultInclude);
	let include = $derived(selected.length ? selected : defaultInclude);

	function href(format: ExportFormat) {
		return api.books.exportUrl(slug, { format, include });
	}

	function toggle(value: ExportInclude) {
		overrides = selected.includes(value)
			? selected.filter((v) => v !== value)
			: [...selected, value];
	}

	function toggleOpen() {
		open = !open;
		copied = '';
	}

	function close() {
		open = false;
	}

	async function copyJson() {
		try {
			const payload = await api.books.exportJson(slug, include);
			await navigator.clipboard.writeText(JSON.stringify(payload, null, 2));
			copied = 'Copied';
		} catch {
			copied = 'Copy failed';
		}
	}

	// Both listeners only exist while the popover is open, so a page with one
	// menu per book section does not accumulate global handlers.
	$effect(() => {
		if (!open) return;
		const onPointer = (event: MouseEvent) => {
			if (root && !root.contains(event.target as Node)) close();
		};
		const onKeydown = (event: KeyboardEvent) => {
			if (event.key === 'Escape') close();
		};
		document.addEventListener('click', onPointer, true);
		document.addEventListener('keydown', onKeydown);
		return () => {
			document.removeEventListener('click', onPointer, true);
			document.removeEventListener('keydown', onKeydown);
		};
	});
</script>

<div class="export" bind:this={root}>
	<button
		class="export-trigger"
		type="button"
		aria-expanded={open}
		aria-haspopup="dialog"
		onclick={toggleOpen}
	>
		{label}
	</button>

	{#if open}
		<div class="export-popover" role="dialog" aria-label="Export options">
			<p class="export-label">Include</p>
			<div class="export-checks">
				{#each ALL as option}
					<label class="export-check">
						<input
							type="checkbox"
							checked={selected.includes(option.value)}
							onchange={() => toggle(option.value)}
						/>
						<span>{option.label}</span>
					</label>
				{/each}
			</div>

			{#if selected.length === 0}
				<p class="export-hint">Nothing ticked, so the default set is exported.</p>
			{/if}

			<p class="export-label">Download</p>
			<div class="export-formats">
				{#each FORMATS as format}
					<a class="export-format" href={href(format.value)} download>{format.label}</a>
				{/each}
			</div>

			<button class="export-copy" type="button" onclick={copyJson}>
				{copied || 'Copy JSON'}
			</button>
		</div>
	{/if}
</div>

<style>
	.export { position: relative; display: inline-block; }
	.export-trigger { font-size: 12px; padding: .25rem .6rem; }
	.export-popover {
		position: absolute;
		right: 0;
		top: calc(100% + .35rem);
		z-index: 60;
		width: 14rem;
		padding: .6rem .75rem .75rem;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--bg-card);
		box-shadow: 0 4px 14px var(--shadow-color);
		text-align: left;
	}
	.export-label {
		font-size: 11px;
		text-transform: uppercase;
		letter-spacing: .06em;
		color: var(--text-muted);
		margin-bottom: .35rem;
	}
	.export-checks { display: flex; flex-direction: column; gap: .2rem; margin-bottom: .7rem; }
	.export-check { display: flex; align-items: center; gap: .4rem; font-size: 13px; cursor: pointer; }
	.export-hint { font-size: 11px; color: var(--text-muted); margin: -.5rem 0 .6rem; }
	.export-formats { display: flex; gap: .35rem; margin-bottom: .6rem; }
	.export-format {
		flex: 1;
		text-align: center;
		font-size: 12px;
		padding: .25rem .3rem;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		color: var(--text);
	}
	.export-format:hover { border-color: var(--text-muted); color: var(--link-hover); }
	.export-copy { width: 100%; font-size: 12px; }
</style>
