import { api } from '$lib/api';
export async function load({ params }) {
	const [screenshots, calendar, readingStats, highlights, clippings] = await Promise.all([
		api.books.screenshots(params.book).catch(() => []),
		api.books.readingCalendar(params.book).catch(() => []),
		api.books.readingStats(params.book).catch(() => null),
		api.books.highlights(params.book).catch(() => []),
		api.books.clippings(params.book).catch(() => []),
	]);
	return { book: params.book, screenshots, calendar, readingStats, highlights, clippings };
}
