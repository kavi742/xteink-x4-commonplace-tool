import { api } from '$lib/api';
export async function load() {
	const [entries, stats] = await Promise.all([
		api.readingLog.list(200).catch(() => []),
		api.readingLog.stats().catch(() => null),
	]);
	return { entries, stats };
}
