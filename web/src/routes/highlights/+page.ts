import { api } from '$lib/api';
export async function load() {
	const highlights = await api.highlights.listAll(200).catch(() => []);
	return { highlights };
}
