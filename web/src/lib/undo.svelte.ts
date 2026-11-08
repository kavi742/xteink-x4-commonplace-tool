/**
 * Undo toast state — one shared instance for the whole app, in the style of
 * panel.svelte.ts. A second instance would mean two toasts fighting over the
 * same corner.
 *
 * The toast hides after ten seconds; the server token lives for 24 hours and
 * the item sits in /trash indefinitely, so dismissing loses nothing.
 */
import { api, type UndoEntry } from '$lib/api';

const TOAST_MS = 10_000;

let _entry = $state<UndoEntry | null>(null);
let _busy = $state(false);
let _timer: ReturnType<typeof setTimeout> | null = null;

function clearTimer() {
	if (_timer !== null) {
		clearTimeout(_timer);
		_timer = null;
	}
}

function hide() {
	clearTimer();
	_entry = null;
}

export const undo = {
	get entry(): UndoEntry | null {
		return _entry;
	},
	get busy(): boolean {
		return _busy;
	},

	offer(entry: UndoEntry) {
		clearTimer();
		_entry = entry;
		_timer = setTimeout(hide, TOAST_MS);
	},

	dismiss() {
		hide();
	},

	/** Pairs with the server's idempotency rather than replacing it. */
	async run() {
		const entry = _entry;
		if (!entry || _busy) return;
		_busy = true;
		try {
			await api.undo.run(entry.token);
		} finally {
			_busy = false;
			hide();
		}
	},

	/** A deletion made on the reader has no page open to raise its toast. */
	async rehydrate() {
		const recent = await api.undo.recent(1).catch(() => ({ items: [] as UndoEntry[] }));
		if (recent.items.length > 0) this.offer(recent.items[0]);
	},

	/** Raise the toast for a token a delete just returned. The label is the
	 * server's, so no toast copy is ever derived from the kind. */
	async offerToken(token: string | null) {
		if (!token) return;
		const recent = await api.undo.recent(5).catch(() => ({ items: [] as UndoEntry[] }));
		const match = recent.items.find((e) => e.token === token);
		if (match) this.offer(match);
	},
};
