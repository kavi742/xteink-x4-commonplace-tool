import { api } from '$lib/api';

export async function load() {
	const articles = await api.articles.list().catch(() => []);
	return { articles };
}
