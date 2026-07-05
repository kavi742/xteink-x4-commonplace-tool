<script lang="ts">
	import { api, type Screenshot, type Highlight } from '$lib/api';
	import { panel } from '$lib/panel.svelte';

	let shot = $state<Screenshot | null>(null);
	let highlights = $state<Highlight[]>([]);
	let saving = $state(false);
	let selectedText = $state('');
	let highlighting = $state(false);

	$effect(() => {
		if (panel.id === null) { shot = null; highlights = []; return; }
		api.screenshots.get(panel.id).then(s => { shot = s; });
		api.highlights.list(panel.id).then(h => { highlights = h; });
	});

	async function save(field: 'ocr_corrected' | 'user_notes', value: string) {
		if (!shot) return;
		saving = true;
		await api.screenshots.update(shot.id, { [field]: value });
		saving = false;
	}

	function handleMouseUp() {
		const sel = window.getSelection();
		selectedText = sel?.toString().trim() ?? '';
	}

	async function addHighlight() {
		if (!shot || !selectedText) return;
		highlighting = true;
		try {
			const h = await api.highlights.create(shot.id, selectedText);
			highlights = [...highlights, h];
			selectedText = '';
			window.getSelection()?.removeAllRanges();
		} finally {
			highlighting = false;
		}
	}

	async function removeHighlight(id: number) {
		await api.highlights.delete(id);
		highlights = highlights.filter(h => h.id !== id);
	}

	/** Render OCR text with <mark> around highlighted passages. */
	function renderOcr(text: string, hl: Highlight[]): string {
		if (!hl.length) return escapeHtml(text);
		let result = escapeHtml(text);
		for (const h of hl) {
			const escaped = escapeHtml(h.selected_text);
			result = result.replace(escaped, `<mark>${escaped}</mark>`);
		}
		return result;
	}

	function escapeHtml(s: string) {
		return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
	}

	let idx = $derived(panel.siblings.indexOf(panel.id ?? -1));
	let total = $derived(panel.siblings.length);
</script>

<div class="panel-header">
	<span style="font-size:12px;color:var(--text-muted)">
		{#if shot}{shot.book_title} · {shot.sync_date}{/if}
	</span>
	<button class="panel-close" onclick={() => panel.close()} aria-label="Close">×</button>
</div>

<div class="panel-body">
	{#if !shot}
		<p style="color:var(--text-muted);font-size:13px">Loading…</p>
	{:else}
		<div class="panel-image">
			<img src={api.screenshots.imageUrl(shot.id)} alt="" />
		</div>

		<p class="panel-meta">
			{shot.book_title} · {shot.sync_date}
			{#if shot.device_path}
				· <span style="font-family:var(--font-mono);font-size:11px">{shot.device_path.split('/').pop()}</span>
			{/if}
		</p>

		{#if shot.ocr_text}
			<div>
				<div style="display:flex;align-items:baseline;justify-content:space-between;margin-bottom:.3rem">
					<p class="panel-label">OCR text</p>
					{#if selectedText}
						<button
							onclick={addHighlight}
							disabled={highlighting}
							style="font-size:11px;padding:.15rem .4rem;background:var(--active-bg)"
						>
							{highlighting ? 'Saving…' : '✦ Highlight'}
						</button>
					{/if}
				</div>
				<!-- svelte-ignore a11y-no-static-element-interactions -->
				<pre
					class="panel-ocr"
					onmouseup={handleMouseUp}
					style="cursor:text"
				>{@html renderOcr(shot.ocr_text, highlights)}</pre>

				{#if highlights.length > 0}
					<div style="margin-top:.5rem;display:flex;flex-direction:column;gap:.3rem">
						{#each highlights as h}
							<div style="display:flex;align-items:flex-start;gap:.4rem;font-size:12px">
								<mark style="flex:1;font-family:var(--font-serif);line-height:1.5;padding:.1rem .3rem">{h.selected_text}</mark>
								<button
									onclick={() => removeHighlight(h.id)}
									style="border:none;background:none;color:var(--text-muted);font-size:14px;padding:0;line-height:1;cursor:pointer"
									title="Remove highlight"
								>×</button>
							</div>
						{/each}
					</div>
				{/if}
			</div>
		{/if}

		<div>
			<p class="panel-label">Correction {saving ? '(saving…)' : ''}</p>
			<textarea
				rows="4"
				value={shot.ocr_corrected ?? ''}
				placeholder="Optional OCR correction…"
				onblur={(e) => save('ocr_corrected', e.currentTarget.value)}
			></textarea>
		</div>

		<div>
			<p class="panel-label">Notes</p>
			<textarea
				rows="3"
				value={shot.user_notes ?? ''}
				placeholder="Personal notes…"
				onblur={(e) => save('user_notes', e.currentTarget.value)}
			></textarea>
		</div>

		{#if total > 1}
			<div class="panel-nav">
				<button onclick={() => panel.prev()} disabled={idx <= 0}>← Prev</button>
				<span>{idx + 1} / {total}</span>
				<button onclick={() => panel.next()} disabled={idx >= total - 1}>Next →</button>
			</div>
		{/if}
	{/if}
</div>

<style>
	mark {
		background: #fff176;
		border-radius: 2px;
	}
	@media (prefers-color-scheme: dark) {
		mark { background: #6b5900; color: #fff; }
	}
</style>
