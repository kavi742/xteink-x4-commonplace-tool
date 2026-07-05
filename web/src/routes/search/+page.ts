import { api } from '$lib/api';
import type { SearchResult } from '$lib/api';

export async function load({ url }) {
	const q = url.searchParams.get('q') ?? '';
	const notesOnly = url.searchParams.get('notes_only') === '1';
	if (!q.trim() && !notesOnly) return { q, notesOnly, results: [] as SearchResult[] };
	const results = await api.search(q, 50, notesOnly).catch(() => [] as SearchResult[]);
	return { q, notesOnly, results };
}
