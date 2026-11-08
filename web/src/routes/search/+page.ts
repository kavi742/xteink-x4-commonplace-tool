import { api } from '$lib/api';
import type { SearchKind, SearchResponse } from '$lib/api';

const KINDS: readonly string[] = ['screenshot', 'note', 'highlight', 'clipping', 'writing'];

function empty(q: string): SearchResponse {
	return {
		q,
		kinds: [],
		book: '',
		limit: 50,
		offset: 0,
		total: 0,
		facets: { screenshot: 0, note: 0, highlight: 0, clipping: 0, writing: 0 },
		results: [],
	};
}

export async function load({ url }) {
	const q = url.searchParams.get('q') ?? '';
	const book = url.searchParams.get('book') ?? '';
	const notesOnly = url.searchParams.get('notes_only') === '1';
	const kinds = (url.searchParams.get('kinds') ?? '')
		.split(',')
		.filter((k): k is SearchKind => KINDS.includes(k));
	// The old /search?notes_only=1 link is now just a kind filter with no query.
	if (notesOnly && kinds.length === 0) kinds.push('note');

	if (!q.trim() && kinds.length === 0) return { q, book, kinds, notesOnly, response: empty(q) };

	const response = await api.search(q, { kinds, book }).catch(() => empty(q));
	return { q, book, kinds, notesOnly, response };
}
