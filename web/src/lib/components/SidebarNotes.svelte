<script lang="ts">
	import { browser } from '$app/environment';

	const KEY = 'xteink-sidebar-notes';
	let notes = $state(browser ? (localStorage.getItem(KEY) ?? '') : '');
	let timer: ReturnType<typeof setTimeout>;

	function onChange(e: Event) {
		notes = (e.currentTarget as HTMLTextAreaElement).value;
		clearTimeout(timer);
		timer = setTimeout(() => {
			if (browser) localStorage.setItem(KEY, notes);
		}, 400);
	}
</script>

<div class="notes-widget">
	<p class="section-label" style="margin-top:.75rem">Notes</p>
	<textarea
		rows="5"
		value={notes}
		oninput={onChange}
		placeholder="Quick notes…"
		style="font-size:12px;line-height:1.5;resize:vertical;min-height:80px"
	></textarea>
</div>
