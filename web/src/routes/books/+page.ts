import { api } from '$lib/api';
export async function load() {
	const books = await api.books.list().catch(() => []);
	return { books };
}
