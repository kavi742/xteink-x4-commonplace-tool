import { api } from '$lib/api';
export async function load() {
	const books = await api.tbr.list().catch(() => []);
	return { books };
}
