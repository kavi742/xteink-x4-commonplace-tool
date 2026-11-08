/**
 * Tests for the book card cover.
 *
 * A missing cover must render no image element at all, not an empty box, so the
 * card collapses to its text. A cover that fails to load has to reach the same
 * state, because a broken-image icon is worse than no cover.
 */
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/svelte';
import BookCard from './BookCard.svelte';
import type { Book } from '$lib/api';

function book(over: Partial<Book> = {}): Book {
	return {
		book_title: 'A-Short-Stay-in-Hell',
		slug: 'a-short-stay-in-hell',
		kind: 'book',
		cover_url: null,
		screenshot_count: 2,
		last_synced: null,
		last_date: '2026-08-04',
		percentage_display: 17.1,
		last_read_at: null,
		...over,
	};
}

afterEach(cleanup);

describe('BookCard cover', () => {
	it('renders no image element when there is no cover', () => {
		const { container } = render(BookCard, { props: { book: book() } });
		expect(container.querySelector('img')).toBeNull();
	});

	it('renders the cover from the url the backend gave it', () => {
		render(BookCard, {
			props: { book: book({ cover_url: '/api/books/a-short-stay-in-hell/cover?v=9f1c' }) },
		});
		const img = screen.getByRole('img');
		expect(img).toHaveAttribute('src', '/api/books/a-short-stay-in-hell/cover?v=9f1c');
		expect(img).toHaveAttribute('alt', 'A-Short-Stay-in-Hell');
		expect(img).toHaveAttribute('loading', 'lazy');
		expect(img).toHaveAttribute('decoding', 'async');
	});

	it('drops the image when it fails to load', async () => {
		const { container } = render(BookCard, {
			props: { book: book({ cover_url: '/api/books/a-short-stay-in-hell/cover?v=gone' }) },
		});
		const img = container.querySelector('img');
		expect(img).not.toBeNull();
		await fireEvent.error(img!);
		expect(container.querySelector('img')).toBeNull();
	});

	it('still renders the title and meta without a cover', () => {
		render(BookCard, { props: { book: book() } });
		expect(screen.getByText('A-Short-Stay-in-Hell')).toBeInTheDocument();
		expect(screen.getByText(/2 screenshots/)).toBeInTheDocument();
	});
});
