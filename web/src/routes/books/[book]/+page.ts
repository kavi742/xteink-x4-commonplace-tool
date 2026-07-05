import { api } from '$lib/api';
export async function load({ params }) {
	const screenshots = await api.books.screenshots(params.book).catch(() => []);
	return { book: params.book, screenshots };
}
