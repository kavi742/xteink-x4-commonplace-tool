import { api } from '$lib/api';
export async function load() {
	// Concurrent: /api/books is already the slowest list endpoint on this page.
	const [books, onThisDay] = await Promise.all([
		api.books.list().catch(() => []),
		api.onThisDay().catch(() => null),
	]);
	return { books, onThisDay };
}
