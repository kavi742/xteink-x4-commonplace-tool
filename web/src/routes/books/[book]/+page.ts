import { api } from '$lib/api';
export async function load({ params }) {
	const [screenshots, calendar] = await Promise.all([
		api.books.screenshots(params.book).catch(() => []),
		api.books.readingCalendar(params.book).catch(() => []),
	]);
	return { book: params.book, screenshots, calendar };
}
