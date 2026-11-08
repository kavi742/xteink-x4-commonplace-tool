/**
 * Tests for src/lib/components/UndoToast.svelte and the shared undo store.
 *
 * `label` is rendered verbatim: the server owns the copy, so the device wording
 * reaches the user without the client deriving anything from `kind`.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/svelte';
import UndoToast from '$lib/components/UndoToast.svelte';
import { undo } from '$lib/undo.svelte';
import { api } from '$lib/api';
import type { UndoEntry } from '$lib/api';

vi.mock('$app/navigation', () => ({ invalidateAll: vi.fn(async () => {}) }));

function entry(over: Partial<UndoEntry> = {}): UndoEntry {
	return {
		token: '7f3a1c9b',
		kind: 'clipping_delete',
		label: 'Deleted 1 clipping',
		created_at: 1762502692,
		expires_at: 1762589092,
		...over,
	};
}

beforeEach(() => {
	vi.restoreAllMocks();
	undo.dismiss();
});

afterEach(() => {
	cleanup();
	undo.dismiss();
	vi.restoreAllMocks();
});

describe('UndoToast', () => {
	it('renders nothing when there is no entry', () => {
		const { container } = render(UndoToast);
		expect(container.querySelector('.undo-toast')).toBeNull();
	});

	it('renders the label verbatim', async () => {
		render(UndoToast);
		undo.offer(entry());
		expect(await screen.findByText('Deleted 1 clipping')).toBeInTheDocument();
	});

	it('renders the longer device label including the next-sync sentence', async () => {
		render(UndoToast);
		undo.offer(entry({ label: 'The reader deleted 1 clipping. Undo sends it back at the next sync.' }));
		expect(
			await screen.findByText('The reader deleted 1 clipping. Undo sends it back at the next sync.'),
		).toBeInTheDocument();
	});

	it('clicking Undo calls api.undo.run exactly once', async () => {
		const spy = vi.spyOn(api.undo, 'run').mockResolvedValue({
			ok: true,
			already_undone: false,
			kind: 'clipping_delete',
			label: 'Deleted 1 clipping',
			result: {},
		});
		render(UndoToast);
		undo.offer(entry());

		const button = await screen.findByRole('button', { name: 'Undo' });
		button.click();

		await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
		expect(spy).toHaveBeenCalledWith('7f3a1c9b');
	});

	it('does not fire a second request while the first is in flight', async () => {
		let release: () => void = () => {};
		const spy = vi.spyOn(api.undo, 'run').mockReturnValue(
			new Promise((resolve) => {
				release = () => resolve({} as never);
			}),
		);
		render(UndoToast);
		undo.offer(entry());

		const button = await screen.findByRole('button', { name: 'Undo' });
		button.click();
		button.click();
		await waitFor(() => expect(undo.busy).toBe(true));
		release();

		await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
	});

	it('dismiss hides the toast without touching the server token', async () => {
		const spy = vi.spyOn(api.undo, 'run');
		render(UndoToast);
		undo.offer(entry());
		await screen.findByText('Deleted 1 clipping');

		(await screen.findByRole('button', { name: 'Dismiss' })).click();

		await waitFor(() => expect(screen.queryByText('Deleted 1 clipping')).toBeNull());
		expect(spy).not.toHaveBeenCalled();
	});

	it('is announced politely without stealing focus', async () => {
		render(UndoToast);
		undo.offer(entry());
		const status = await screen.findByRole('status');
		expect(status).toHaveAttribute('aria-live', 'polite');
	});
});

describe('undo store', () => {
	it('offerToken raises the toast with the server label for that token', async () => {
		vi.spyOn(api.undo, 'recent').mockResolvedValue({
			items: [entry({ token: 'other' }), entry({ token: 'mine', label: 'Deleted 1 highlight' })],
		});

		await undo.offerToken('mine');

		expect(undo.entry?.token).toBe('mine');
		expect(undo.entry?.label).toBe('Deleted 1 highlight');
	});

	it('offerToken does nothing for a null token', async () => {
		const spy = vi.spyOn(api.undo, 'recent');
		await undo.offerToken(null);
		expect(spy).not.toHaveBeenCalled();
		expect(undo.entry).toBeNull();
	});

	it('rehydrate picks up a deletion made while no page was open', async () => {
		vi.spyOn(api.undo, 'recent').mockResolvedValue({
			items: [entry({ label: 'The reader deleted 2 clippings. Undo sends it back at the next sync.' })],
		});

		await undo.rehydrate();

		expect(undo.entry?.label).toContain('The reader deleted 2 clippings');
	});

	it('rehydrate stays quiet when nothing is pending', async () => {
		vi.spyOn(api.undo, 'recent').mockResolvedValue({ items: [] });
		await undo.rehydrate();
		expect(undo.entry).toBeNull();
	});
});
