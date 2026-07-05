import { api } from '$lib/api';
import type { SearchResult } from '$lib/api';

export async function load({ url }) {
	const q = url.searchParams.get('q') ?? '';
	if (!q.trim()) return { q, results: [] as SearchResult[] };
	const results = await api.search(q).catch(() => [] as SearchResult[]);
	return { q, results };
}
