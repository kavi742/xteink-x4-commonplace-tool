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
