/**
 * Tests for the /trash page.
 *
 * The purge confirmation is a second gate on top of the server's `confirm=1`,
 * so the API must not be called until the user confirms.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/svelte';
import TrashPage from './+page.svelte';
import { api } from '$lib/api';
import type { TrashResponse } from '$lib/api';

vi.mock('$app/navigation', () => ({ invalidateAll: vi.fn(async () => {}) }));

function trash(over: Partial<TrashResponse> = {}): TrashResponse {
	return {
		counts: { highlights: 1, clippings: 1, writing: 0 },
		highlights: [
			{
				id: 41,
				screenshot_id: 112,
				selected_text: 'A sentence worth keeping.',
				book_title: 'Pastoral',
				sync_date: '2026-07-04',
				deleted_at: '2026-11-07 09:02:11',
			},
		],
		clippings: [
			{
				id: 184,
				clip_id: 'cp-0f3a',
				document: 'aaaa1111',
				book_title: 'The Hidden Keys',
				book_slug: 'the-hidden-keys',
				chapter: 'Chapter One',
				text: 'An excerpt saved on the reader.',
				deleted_at: '2026-11-07 09:04:52',
				deleted_source: 'ui',
				restorable: true,
			},
		],
		writing: [],
		...over,
	};
}

beforeEach(() => vi.restoreAllMocks());
afterEach(() => {
	cleanup();
	vi.restoreAllMocks();
});

describe('/trash', () => {
	it('renders every non-empty bucket with its count', () => {
		render(TrashPage, { props: { data: { books: [], trash: trash() } } });

		expect(screen.getByText('Highlights')).toBeInTheDocument();
		expect(screen.getByText('Clippings')).toBeInTheDocument();
		expect(screen.getByText('A sentence worth keeping.')).toBeInTheDocument();
		expect(screen.getByText('An excerpt saved on the reader.')).toBeInTheDocument();
	});

	it('renders no section at all for an empty bucket', () => {
		render(TrashPage, { props: { data: { books: [], trash: trash() } } });
		expect(screen.queryByText('Writing')).toBeNull();
	});

	it('renders the writing bucket once it has rows', () => {
		const data = trash({
			counts: { highlights: 0, clippings: 0, writing: 1 },
			highlights: [],
			clippings: [],
			writing: [
				{
					id: 12,
					book_title: 'Pastoral',
					book_slug: 'pastoral',
					title: 'On attention',
					entry_date: '2026-11-07',
					excerpt: 'What stayed with me was this...',
					ref_count: 3,
					deleted_at: '2026-11-07 09:06:30',
					restorable: true,
				},
			],
		});
		render(TrashPage, { props: { data: { books: [], trash: data } } });

		expect(screen.getByText('Writing')).toBeInTheDocument();
		expect(screen.getByText('On attention')).toBeInTheDocument();
	});

	it('says the trash is empty when every bucket is', () => {
		const data = trash({
			counts: { highlights: 0, clippings: 0, writing: 0 },
			highlights: [],
			clippings: [],
		});
		render(TrashPage, { props: { data: { books: [], trash: data } } });
		expect(screen.getByText(/Nothing in the trash/)).toBeInTheDocument();
	});

	it('the purge button opens a confirmation and calls nothing until confirmed', async () => {
		const spy = vi.spyOn(api.trash, 'purgeClipping');
		render(TrashPage, { props: { data: { books: [], trash: trash() } } });

		screen.getAllByRole('button', { name: 'Purge' })[1].click();

		await waitFor(() => expect(screen.getByText('Purge permanently?')).toBeInTheDocument());
		expect(spy).not.toHaveBeenCalled();
		expect(screen.getByText(/tombstone row is kept/)).toBeInTheDocument();
	});

	it('confirming the purge calls the API once', async () => {
		const spy = vi.spyOn(api.trash, 'purgeClipping').mockResolvedValue({
			purged: 184,
			row_retained: true,
			undo_token: null,
		});
		vi.spyOn(api.trash, 'get').mockResolvedValue(trash());
		render(TrashPage, { props: { data: { books: [], trash: trash() } } });

		screen.getAllByRole('button', { name: 'Purge' })[1].click();
		(await screen.findByRole('button', { name: 'Purge permanently' })).click();

		await waitFor(() => expect(spy).toHaveBeenCalledWith(184));
		expect(spy).toHaveBeenCalledTimes(1);
	});

	it('cancelling the purge closes the dialog without calling the API', async () => {
		const spy = vi.spyOn(api.trash, 'purgeHighlight');
		render(TrashPage, { props: { data: { books: [], trash: trash() } } });

		screen.getAllByRole('button', { name: 'Purge' })[0].click();
		(await screen.findByRole('button', { name: 'Cancel' })).click();

		await waitFor(() => expect(screen.queryByText('Purge permanently?')).toBeNull());
		expect(spy).not.toHaveBeenCalled();
	});

	it('a clipping whose text was discarded cannot be restored', () => {
		const data = trash();
		data.clippings[0].restorable = false;
		render(TrashPage, { props: { data: { books: [], trash: data } } });

		const restore = screen.getAllByRole('button', { name: 'Restore' })[1];
		expect(restore).toBeDisabled();
		expect(restore).toHaveAttribute('title', expect.stringContaining('nothing to restore'));
	});

	it('restoring a highlight calls the restore route', async () => {
		const spy = vi.spyOn(api.trash, 'restoreHighlight').mockResolvedValue({} as never);
		vi.spyOn(api.trash, 'get').mockResolvedValue(trash());
		render(TrashPage, { props: { data: { books: [], trash: trash() } } });

		screen.getAllByRole('button', { name: 'Restore' })[0].click();

		await waitFor(() => expect(spy).toHaveBeenCalledWith(41));
	});
});
