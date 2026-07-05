import { api } from '$lib/api';
export async function load() {
	const [aliases, unresolved] = await Promise.all([
		api.aliases.list().catch(() => []),
		api.aliases.listUnresolved().catch(() => []),
	]);
	return { aliases, unresolved };
}
