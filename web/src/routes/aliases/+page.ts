import { api } from '$lib/api';
export async function load() {
	const aliases = await api.aliases.list().catch(() => []);
	return { aliases };
}
