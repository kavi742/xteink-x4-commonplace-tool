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

		search: (q: string, limit = 50) =>
			get<SearchResult[]>(`/api/search?q=${encodeURIComponent(q)}&limit=${limit}`),

		readingLog: {
			list: (limit = 100) => get<ProgressEntry[]>(`/api/reading-log?limit=${limit}`),
		},

		aliases: {
			list: () => get<Alias[]>('/api/aliases'),
			set: (hash: string, title: string, filename = '') =>
				put<Alias>(`/api/aliases/${hash}`, { title, filename }),
		},
	};
}

export type { HighlightWithMeta };
export { createApi };
export const api = createApi();
