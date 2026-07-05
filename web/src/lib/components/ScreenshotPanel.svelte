<script lang="ts">
	import { api, type Screenshot } from '$lib/api';
	import { panel } from '$lib/panel.svelte';

	let shot = $state<Screenshot | null>(null);
	let saving = $state(false);

	$effect(() => {
		if (panel.id === null) { shot = null; return; }
		api.screenshots.get(panel.id).then(s => { shot = s; });
	});

	async function save(field: 'ocr_corrected' | 'user_notes', value: string) {
		if (!shot) return;
		saving = true;
		await api.screenshots.update(shot.id, { [field]: value });
		saving = false;
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
				<p class="panel-label">OCR text (original)</p>
				<pre class="panel-ocr">{shot.ocr_text}</pre>
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
