import { api } from '$lib/api';
export async function load() {
	const entries = await api.readingLog.list(200).catch(() => []);
	return { entries };
}
