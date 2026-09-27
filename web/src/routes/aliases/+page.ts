import { api } from '$lib/api';
export async function load() {
	const [aliases, unresolved, devices] = await Promise.all([
		api.aliases.list().catch(() => []),
		api.aliases.listUnresolved().catch(() => []),
		api.devices.list().catch(() => []),
	]);
	return { aliases, unresolved, devices };
}
