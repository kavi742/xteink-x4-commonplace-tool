/**
 * Typed fetch wrappers for the xteink FastAPI backend.
 * All paths are relative — in dev, vite proxies /api to localhost:8090.
 * Pass a custom fetch (from SvelteKit load functions) for SSR.
 */

export interface Book {
	book_title: string;
	screenshot_count: number;
	last_synced: string;
	last_date: string;
}

export interface Screenshot {
	id: number;
	device_path: string;
	content_hash: string;
	synced_at: string;
	book_title: string;
	sync_date: string;
	ocr_text: string | null;
	vault_png_path: string;
	ocr_corrected: string | null;
	user_notes: string | null;
}

export interface ProgressEntry {
	id: number;
	document: string;
	progress: string;
	percentage: number;
	percentage_display: number;
	title_resolved: string | null;
	at: string;
	total_pages: number | null;
	page: number | null;
	page_source: string | null;
}

export interface ReadingCalendarDay {
	date: string;
	percent_read: number;
	start_pct: number;
	end_pct: number;
	sessions: number;
	end_page: number | null;
	pages_read: number | null;
}

export interface ReadingStats {
	books: { started: number; in_progress: number; finished: number };
	read_pct: { today: number; week: number; month: number };
	pages_read: { today: number; week: number; month: number };
	books_read: { today: number; week: number; month: number; year: number; all_time: number };
}

export interface BookReadingStats {
	total_pages: number | null;
	page_source: string | null;
	current_pct: number;
	current_page: number | null;
	sessions: number;
	days_read: number;
	finished: boolean;
}

export interface Alias {
	hash: string;
	title: string;
	filename: string;
	resolved_by: string;
}

export interface Highlight {
	id: number;
	screenshot_id: number;
	selected_text: string;
	bbox_json: string;
	img_w: number;
	img_h: number;
	created_at: string;
}

export interface SearchResult extends Screenshot {
	match_fields: string[];
	snippet: string;
	highlight_matches: string[];
}

export interface TbrBook {
	id: number;
	title: string;
	author: string;
	source_url: string;
	notes: string;
	status: 'queued' | 'reading' | 'done';
	sort_order: number;
	added_at: string;
}

export interface OlBook {
	title: string;
	author: string;
	year: number | null;
	cover_url: string | null;
	ol_key: string;
}

export interface StatusResponse {
	screenshots: {
		total: number;
		last_sync_at: string | null;
		last_book: string | null;
		today_count: number;
		books_today: string[];
	};
	koreader: {
		total_updates: number;
		recent: { document: string; title: string; percentage: number; at: string }[];
	};
}

type Fetch = typeof fetch;

function createApi(customFetch: Fetch = fetch) {
	async function get<T>(url: string): Promise<T> {
		const res = await customFetch(url);
		if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${url}`);
		return res.json() as Promise<T>;
	}

	async function put<T>(url: string, body: unknown): Promise<T> {
		const res = await customFetch(url, {
			method: 'PUT',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(body),
		});
		if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${url}`);
		return res.json() as Promise<T>;
	}

	async function post<T>(url: string, body: unknown): Promise<T> {
		const res = await customFetch(url, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(body),
		});
		if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${url}`);
		return res.json() as Promise<T>;
	}

	async function del<T>(url: string): Promise<T> {
		const res = await customFetch(url, { method: 'DELETE' });
		if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${url}`);
		return res.json() as Promise<T>;
	}

	return {
		status: () => get<StatusResponse>('/status'),

		books: {
			list: () => get<Book[]>('/api/books'),
			screenshots: (slug: string) =>
				get<Screenshot[]>(`/api/books/${encodeURIComponent(slug)}/screenshots`),
			readingCalendar: (slug: string) =>
				get<ReadingCalendarDay[]>(`/api/books/${encodeURIComponent(slug)}/reading-calendar`),
			readingStats: (slug: string) =>
				get<BookReadingStats>(`/api/books/${encodeURIComponent(slug)}/reading-stats`),
		},

		screenshots: {
			get: (id: number) => get<Screenshot>(`/api/screenshots/${id}`),
			imageUrl: (id: number) => `/api/screenshots/${id}/image`,
			update: (id: number, body: { ocr_corrected?: string; user_notes?: string }) =>
				put<{ updated: number }>(`/api/screenshots/${id}`, body),
		},

		highlights: {
			list: (screenshotId: number) =>
				get<Highlight[]>(`/api/screenshots/${screenshotId}/highlights`),
			listAll: (limit = 100) =>
				get<HighlightWithMeta[]>(`/api/highlights?limit=${limit}`),
			create: (screenshotId: number, selectedText: string) =>
				post<Highlight>(`/api/screenshots/${screenshotId}/highlights`, { selected_text: selectedText }),
			delete: (id: number) =>
				del<{ deleted: number }>(`/api/highlights/${id}`),
		},

		search: (q: string, limit = 50, notesOnly = false) => {
			const params = new URLSearchParams({ limit: String(limit) });
			if (q.trim()) params.set('q', q);
			if (notesOnly) params.set('notes_only', '1');
			return get<SearchResult[]>(`/api/search?${params}`);
		},

		tbr: {
			list: () => get<TbrBook[]>('/api/tbr'),
			search: (q: string) => get<OlBook[]>(`/api/tbr/search?q=${encodeURIComponent(q)}`),
			add: (body: { title: string; author?: string; source_url?: string; notes?: string }) =>
				post<TbrBook>('/api/tbr', body),
			update: (id: number, body: Partial<TbrBook>) =>
				put<{ updated: number }>(`/api/tbr/${id}`, body),
			delete: (id: number) =>
				del<{ deleted: number }>(`/api/tbr/${id}`),
		},

		readingLog: {
			list: (limit = 100) => get<ProgressEntry[]>(`/api/reading-log?limit=${limit}`),
			stats: () => get<ReadingStats>('/api/reading-stats'),
		},

		aliases: {
			list: () => get<Alias[]>('/api/aliases'),
			listUnresolved: () => get<{document:string; percentage_display:number; last_seen:number}[]>('/api/aliases/unresolved'),
			set: (hash: string, title: string, filename = '') =>
				put<Alias>(`/api/aliases/${hash}`, { title, filename }),
		},
	};
}

export type { HighlightWithMeta };
export { createApi };
export const api = createApi();
