import { api } from '$lib/api';
export async function load() {
	const clippings = await api.clippings.listAll(500).catch(() => []);
	return { clippings };
}
