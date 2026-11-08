/**
 * Tests for src/lib/api.ts
 *
 * Mocks global fetch; verifies correct URLs, methods, and JSON decoding.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { api } from '$lib/api';

function mockFetch(data: unknown, ok = true, status = 200) {
	return vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
		ok,
		status,
		statusText: ok ? 'OK' : 'Not Found',
		json: async () => data,
	} as Response);
}

beforeEach(() => vi.restoreAllMocks());
afterEach(() => vi.restoreAllMocks());

// ------------------------------------------------------------------ //
// /status                                                              //
// ------------------------------------------------------------------ //

describe('api.status', () => {
	it('GET /status and returns parsed body', async () => {
		const stub = { screenshots: { total: 5 }, koreader: { total_updates: 2, recent: [] } };
		const spy = mockFetch(stub);

		const result = await api.status();

		expect(spy).toHaveBeenCalledWith('/status');
		expect(result.screenshots.total).toBe(5);
		expect(result.koreader.total_updates).toBe(2);
	});

	it('throws on non-ok response', async () => {
		mockFetch({}, false, 500);
		await expect(api.status()).rejects.toThrow('500');
	});
});

// ------------------------------------------------------------------ //
// /api/books                                                           //
// ------------------------------------------------------------------ //

describe('api.books.list', () => {
	it('GET /api/books', async () => {
		const stub = [{ book_title: 'Pastoral', screenshot_count: 25 }];
		const spy = mockFetch(stub);

		const result = await api.books.list();

		expect(spy).toHaveBeenCalledWith('/api/books');
		expect(result).toHaveLength(1);
		expect(result[0].book_title).toBe('Pastoral');
	});
});

describe('api.books.screenshots', () => {
	it('GET /api/books/{slug}/screenshots with URL-encoded slug', async () => {
		const stub = [{ id: 1, book_title: 'Fifteen Dogs' }];
		const spy = mockFetch(stub);

		await api.books.screenshots('Fifteen Dogs');

		expect(spy).toHaveBeenCalledWith('/api/books/Fifteen%20Dogs/screenshots');
	});
});

// ------------------------------------------------------------------ //
// /api/screenshots                                                     //
// ------------------------------------------------------------------ //

describe('api.screenshots.get', () => {
	it('GET /api/screenshots/{id}', async () => {
		const stub = { id: 42, book_title: 'Pastoral', ocr_text: 'Apple.' };
		const spy = mockFetch(stub);

		const result = await api.screenshots.get(42);

		expect(spy).toHaveBeenCalledWith('/api/screenshots/42');
		expect(result.id).toBe(42);
		expect(result.ocr_text).toBe('Apple.');
	});
});

describe('api.screenshots.imageUrl', () => {
	it('returns the correct image URL without fetching', () => {
		expect(api.screenshots.imageUrl(7)).toBe('/api/screenshots/7/image');
	});
});

describe('api.screenshots.update', () => {
	it('PUT /api/screenshots/{id} with body', async () => {
		const spy = mockFetch({ updated: 5 });

		await api.screenshots.update(5, { ocr_corrected: 'corrected text' });

		expect(spy).toHaveBeenCalledWith(
			'/api/screenshots/5',
			expect.objectContaining({
				method: 'PUT',
				body: JSON.stringify({ ocr_corrected: 'corrected text' }),
			})
		);
	});
});

// ------------------------------------------------------------------ //
// /api/reading-log                                                     //
// ------------------------------------------------------------------ //

describe('api.readingLog.list', () => {
	it('GET /api/reading-log with default limit', async () => {
		const spy = mockFetch([]);
		await api.readingLog.list();
		expect(spy).toHaveBeenCalledWith('/api/reading-log?limit=100');
	});

	it('respects custom limit', async () => {
		const spy = mockFetch([]);
		await api.readingLog.list(10);
		expect(spy).toHaveBeenCalledWith('/api/reading-log?limit=10');
	});
});

// ------------------------------------------------------------------ //
// /api/aliases                                                         //
// ------------------------------------------------------------------ //

describe('api.aliases.list', () => {
	it('GET /api/aliases', async () => {
		const stub = [{ hash: 'abc123', title: 'Pastoral', filename: 'Pastoral.epub' }];
		const spy = mockFetch(stub);

		const result = await api.aliases.list();

		expect(spy).toHaveBeenCalledWith('/api/aliases');
		expect(result[0].title).toBe('Pastoral');
	});
});

describe('api.aliases.set', () => {
	it('PUT /api/aliases/{hash} with title and filename', async () => {
		const spy = mockFetch({ hash: 'abc', title: 'New Book' });

		await api.aliases.set('abc', 'New Book', 'new-book.epub');

		expect(spy).toHaveBeenCalledWith(
			'/api/aliases/abc',
			expect.objectContaining({
				method: 'PUT',
				body: JSON.stringify({ title: 'New Book', filename: 'new-book.epub' }),
			})
		);
	});

	it('defaults filename to empty string', async () => {
		const spy = mockFetch({});

		await api.aliases.set('xyz', 'Some Book');

		expect(spy).toHaveBeenCalledWith(
			'/api/aliases/xyz',
			expect.objectContaining({
				body: JSON.stringify({ title: 'Some Book', filename: '' }),
			})
		);
	});
});

// ------------------------------------------------------------------ //
// /api/trash and /api/undo                                             //
// ------------------------------------------------------------------ //

describe('api.trash', () => {
	it('GET /api/trash', async () => {
		const stub = {
			counts: { highlights: 0, clippings: 0, writing: 0 },
			highlights: [],
			clippings: [],
			writing: [],
		};
		const spy = mockFetch(stub);

		const result = await api.trash.get();

		expect(spy).toHaveBeenCalledWith('/api/trash');
		expect(result.counts.writing).toBe(0);
	});

	it('every purge wrapper appends confirm=1', async () => {
		const highlight = mockFetch({ purged: 41, row_retained: false, undo_token: null });
		await api.trash.purgeHighlight(41);
		expect(highlight).toHaveBeenCalledWith(
			'/api/trash/highlights/41?confirm=1',
			expect.objectContaining({ method: 'DELETE' }),
		);

		const clipping = mockFetch({ purged: 184, row_retained: true, undo_token: null });
		await api.trash.purgeClipping(184);
		expect(clipping).toHaveBeenCalledWith(
			'/api/trash/clippings/184?confirm=1',
			expect.objectContaining({ method: 'DELETE' }),
		);

		const empty = mockFetch({ purged: {}, rows_retained: 0 });
		await api.trash.empty();
		expect(empty).toHaveBeenCalledWith(
			'/api/trash/empty?confirm=1',
			expect.objectContaining({ method: 'POST' }),
		);
	});

	it('POSTs the two restore routes', async () => {
		const highlight = mockFetch({ id: 41 });
		await api.trash.restoreHighlight(41);
		expect(highlight).toHaveBeenCalledWith(
			'/api/highlights/41/restore',
			expect.objectContaining({ method: 'POST' }),
		);

		const clipping = mockFetch({ restored: 184, revision: 914 });
		await api.trash.restoreClipping(184);
		expect(clipping).toHaveBeenCalledWith(
			'/api/clippings/184/restore',
			expect.objectContaining({ method: 'POST' }),
		);
	});
});

describe('api.undo', () => {
	it('POST /api/undo with the token in the body', async () => {
		const spy = mockFetch({
			ok: true,
			already_undone: false,
			kind: 'clipping_delete',
			label: '',
			result: {},
		});

		await api.undo.run('7f3a1c9b');

		expect(spy).toHaveBeenCalledWith(
			'/api/undo',
			expect.objectContaining({
				method: 'POST',
				body: JSON.stringify({ token: '7f3a1c9b' }),
			}),
		);
	});

	it('GET /api/undo/recent?limit=5', async () => {
		const spy = mockFetch({ items: [] });

		await api.undo.recent(5);

		expect(spy).toHaveBeenCalledWith('/api/undo/recent?limit=5');
	});
});

// ------------------------------------------------------------------ //
// /api/books/{slug}/export                                             //
// ------------------------------------------------------------------ //

describe('api.books.exportUrl', () => {
	it('builds the query string for each format without fetching', () => {
		const spy = vi.spyOn(globalThis, 'fetch');

		expect(api.books.exportUrl('Pastoral', { format: 'md', include: ['highlights'] })).toBe(
			'/api/books/Pastoral/export?format=md&include=highlights&group=auto',
		);
		expect(api.books.exportUrl('Pastoral', { format: 'csv', include: ['clippings'] })).toBe(
			'/api/books/Pastoral/export?format=csv&include=clippings&group=auto',
		);
		expect(
			api.books.exportUrl('Pastoral', {
				format: 'json',
				include: ['notes'],
				group: 'chapter',
			}),
		).toBe('/api/books/Pastoral/export?format=json&include=notes&group=chapter');
		expect(spy).not.toHaveBeenCalled();
	});

	it('joins include with commas rather than repeating the param', () => {
		const url = api.books.exportUrl('Pastoral', {
			format: 'md',
			include: ['highlights', 'clippings'],
		});

		expect(url).toContain('include=highlights%2Cclippings');
		expect(url.match(/include=/g)).toHaveLength(1);
	});

	it('URL-encodes the slug', () => {
		expect(
			api.books.exportUrl('Fifteen Dogs', { format: 'md', include: ['highlights'] }),
		).toContain('/api/books/Fifteen%20Dogs/export?');
	});
});

describe('api.books.exportJson', () => {
	it('GETs the json format of the export endpoint', async () => {
		const spy = mockFetch({ book_slug: 'pastoral', groups: [] });

		await api.books.exportJson('Pastoral', ['clippings']);

		expect(spy).toHaveBeenCalledWith(
			'/api/books/Pastoral/export?format=json&include=clippings&group=auto',
		);
	});
});

// ------------------------------------------------------------------ //
// /api/on-this-day and /api/review                                     //
// ------------------------------------------------------------------ //

describe('api.onThisDay', () => {
	it('omits date when not given', async () => {
		const spy = mockFetch({ date: '2026-11-08', total: 0, years: [] });

		await api.onThisDay();

		expect(spy).toHaveBeenCalledWith('/api/on-this-day');
	});

	it('passes an explicit date', async () => {
		const spy = mockFetch({ date: '2025-07-04', total: 0, years: [] });

		await api.onThisDay('2025-07-04');

		expect(spy).toHaveBeenCalledWith('/api/on-this-day?date=2025-07-04');
	});
});

describe('api.review', () => {
	it('defaults to the spaced strategy', async () => {
		const spy = mockFetch({ items: [] });

		await api.review();

		expect(spy).toHaveBeenCalledWith('/api/review?strategy=spaced&limit=10');
	});

	it('builds the query for a seeded random pass', async () => {
		const spy = mockFetch({ items: [] });

		await api.review({ strategy: 'random', seed: '2026-11-07', limit: 3 });

		expect(spy).toHaveBeenCalledWith('/api/review?strategy=random&limit=3&seed=2026-11-07');
	});

	it('joins kinds with commas', async () => {
		const spy = mockFetch({ items: [] });

		await api.review({ kinds: ['highlight', 'clipping'] });

		expect(spy).toHaveBeenCalledWith(
			'/api/review?strategy=spaced&limit=10&kinds=highlight%2Cclipping',
		);
	});
});

describe('api.reviewSeen', () => {
	it('POSTs the rating to the seen endpoint', async () => {
		const spy = mockFetch({ kind: 'highlight', ref_id: 41, interval_days: 2 });

		await api.reviewSeen('highlight', 41, 'good');

		expect(spy).toHaveBeenCalledWith(
			'/api/review/highlight/41/seen',
			expect.objectContaining({
				method: 'POST',
				body: JSON.stringify({ rating: 'good' }),
			}),
		);
	});
});
