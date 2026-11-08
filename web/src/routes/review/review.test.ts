/**
 * Tests for the /review page.
 *
 * One passage is shown at a time, and a rating has to advance even when the
 * write fails, or a server hiccup strands the user on the same card forever.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/svelte';
import ReviewPage from './+page.svelte';
import { api } from '$lib/api';
import type { ReviewItem, ReviewResponse } from '$lib/api';

vi.mock('$app/navigation', () => ({ goto: vi.fn(async () => {}) }));

function reviewItem(over: Partial<ReviewItem> = {}): ReviewItem {
	return {
		kind: 'highlight',
		ref_id: 41,
		screenshot_id: 112,
		book_title: 'Pastoral',
		book_slug: 'pastoral',
		date: '2026-07-04',
		text: 'A sentence worth keeping.',
		shown_count: 0,
		last_shown: null,
		next_due: '',
		...over,
	};
}

function queue(items: ReviewItem[]): ReviewResponse {
	return {
		strategy: 'spaced',
		limit: 20,
		kinds: ['highlight', 'clipping'],
		total_candidates: items.length,
		items,
	};
}

function mount(items: ReviewItem[]) {
	return render(ReviewPage, {
		props: { data: { books: [], strategy: 'spaced' as const, queue: queue(items) } },
	});
}

beforeEach(() => vi.restoreAllMocks());
afterEach(() => {
	cleanup();
	vi.restoreAllMocks();
});

describe('/review', () => {
	it('shows one item at a time', () => {
		mount([reviewItem(), reviewItem({ ref_id: 42, text: 'A second passage.' })]);

		expect(screen.getByText('A sentence worth keeping.')).toBeTruthy();
		expect(screen.queryByText('A second passage.')).toBeNull();
	});

	it('clicking Good records the rating and advances', async () => {
		const seen = vi
			.spyOn(api, 'reviewSeen')
			.mockResolvedValue({ kind: 'highlight', ref_id: 41, shown_count: 1,
				interval_days: 1, next_due: '2026-11-09' });
		mount([reviewItem(), reviewItem({ ref_id: 42, text: 'A second passage.' })]);

		await fireEvent.click(screen.getByRole('button', { name: 'Good' }));

		expect(seen).toHaveBeenCalledWith('highlight', 41, 'good');
		await waitFor(() => expect(screen.getByText('A second passage.')).toBeTruthy());
	});

	it('Again sends the other rating', async () => {
		const seen = vi
			.spyOn(api, 'reviewSeen')
			.mockResolvedValue({ kind: 'highlight', ref_id: 41, shown_count: 1,
				interval_days: 1, next_due: '2026-11-09' });
		mount([reviewItem()]);

		await fireEvent.click(screen.getByRole('button', { name: 'Again' }));

		expect(seen).toHaveBeenCalledWith('highlight', 41, 'again');
	});

	it('advances even when the write fails', async () => {
		vi.spyOn(api, 'reviewSeen').mockRejectedValue(new Error('500'));
		mount([reviewItem(), reviewItem({ ref_id: 42, text: 'A second passage.' })]);

		await fireEvent.click(screen.getByRole('button', { name: 'Good' }));

		await waitFor(() => expect(screen.getByText('A second passage.')).toBeTruthy());
	});

	it('Skip advances without recording anything', async () => {
		const seen = vi.spyOn(api, 'reviewSeen');
		mount([reviewItem(), reviewItem({ ref_id: 42, text: 'A second passage.' })]);

		await fireEvent.click(screen.getByRole('button', { name: 'Skip' }));

		expect(seen).not.toHaveBeenCalled();
		await waitFor(() => expect(screen.getByText('A second passage.')).toBeTruthy());
	});

	it('shows the completion state once the queue empties', async () => {
		vi.spyOn(api, 'reviewSeen').mockResolvedValue({ kind: 'highlight', ref_id: 41,
			shown_count: 1, interval_days: 1, next_due: '2026-11-09' });
		mount([reviewItem()]);

		await fireEvent.click(screen.getByRole('button', { name: 'Good' }));

		await waitFor(() =>
			expect(
				screen.getByText('Reviewed 1 passage. Nothing else is due.'),
			).toBeTruthy(),
		);
	});

	it('says nothing is due when the queue starts empty', () => {
		mount([]);
		expect(screen.getByText('Nothing is due for review right now.')).toBeTruthy();
	});

	it('reports a queue that could not be loaded', () => {
		render(ReviewPage, {
			props: { data: { books: [], strategy: 'spaced' as const, queue: null } },
		});
		expect(screen.getByText('The review queue could not be loaded.')).toBeTruthy();
	});
});
