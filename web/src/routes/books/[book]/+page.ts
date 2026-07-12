import { api } from '$lib/api';
export async function load({ params }) {
	const [screenshots, calendar, readingStats] = await Promise.all([
		api.books.screenshots(params.book).catch(() => []),
		api.books.readingCalendar(params.book).catch(() => []),
		api.books.readingStats(params.book).catch(() => null),
	]);
	return { book: params.book, screenshots, calendar, readingStats };
}
