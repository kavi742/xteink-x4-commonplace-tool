/**
 * Typed fetch wrappers for the xteink FastAPI backend.
 * All paths are relative — in dev, vite proxies /api to localhost:8090.
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

async function get<T>(url: string): Promise<T> {
	const res = await fetch(url);
	if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${url}`);
	return res.json() as Promise<T>;
}

async function put<T>(url: string, body: unknown): Promise<T> {
	const res = await fetch(url, {
		method: 'PUT',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(body),
	});
	if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${url}`);
	return res.json() as Promise<T>;
}

export const api = {
	status: () => get<StatusResponse>('/status'),

	books: {
		list: () => get<Book[]>('/api/books'),
		screenshots: (slug: string) => get<Screenshot[]>(`/api/books/${encodeURIComponent(slug)}/screenshots`),
	},

	screenshots: {
		get: (id: number) => get<Screenshot>(`/api/screenshots/${id}`),
		imageUrl: (id: number) => `/api/screenshots/${id}/image`,
		update: (id: number, body: { ocr_corrected?: string; user_notes?: string }) =>
			put<{ updated: number }>(`/api/screenshots/${id}`, body),
	},

	readingLog: {
		list: (limit = 100) => get<ProgressEntry[]>(`/api/reading-log?limit=${limit}`),
	},

	aliases: {
		list: () => get<Alias[]>('/api/aliases'),
		set: (hash: string, title: string, filename = '') =>
			put<Alias>(`/api/aliases/${hash}`, { title, filename }),
	},
};
