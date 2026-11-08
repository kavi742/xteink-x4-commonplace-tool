/**
 * Typed fetch wrappers for the xteink FastAPI backend.
 * All paths are relative — in dev, vite proxies /api to localhost:8090.
 * Pass a custom fetch (from SvelteKit load functions) for SSR.
 */

export interface Book {
	book_title: string;
	slug: string;
	/** Every Instapaper article collapses into one `articles` entry. */
	kind: 'book' | 'articles';
	/** Always a path on this backend, never an external URL, so it is safe to render directly. */
	cover_url: string | null;
	screenshot_count: number;
	article_count?: number;
	last_synced: string | null;
	last_date: string | null;
	percentage_display: number | null;
	last_read_at: number | null;
}

/** One article as listed by /api/articles. */
export interface Article {
	slug: string;
	title: string;
	hashes: string[];
	screenshot_count: number;
	clipping_count: number;
	percentage_display: number | null;
	last_read_at: number | null;
	finished: boolean;
	total_pages: number | null;
	page: number | null;
}

export interface ArticleDetail {
	slug: string;
	title: string;
	hashes: string[];
	percentage_display: number | null;
	last_read_at: number | null;
	finished: boolean;
	total_pages: number | null;
	page: number | null;
	screenshots: Screenshot[];
	clippings: Clipping[];
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
	book_slug: string | null;
	at: string;
	device: string | null;
	device_id: string | null;
	total_pages: number | null;
	page: number | null;
	page_source: string | null;
	finishes_book?: boolean;
}

export interface SyncDevice {
	device_id: string;
	device: string;
	first_seen: number;
	last_seen: number;
	update_count: number;
	book_count: number;
	scannable: boolean;
	last_document: string | null;
	last_percentage_display: number | null;
}

export interface UnresolvedAlias {
	document: string;
	percentage_display: number;
	last_seen: number;
	device: string;
	device_id: string;
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
	/** books.*, books_read.* and read_pct.* exclude articles; pages_read.* includes them. */
	books: { started: number; in_progress: number; finished: number };
	read_pct: { today: number; week: number; month: number };
	pages_read: { today: number; week: number; month: number };
	books_read: { today: number; week: number; month: number; year: number; all_time: number };
	articles?: { started: number; in_progress: number; finished: number };
	articles_read?: { today: number; week: number; month: number; year: number; all_time: number };
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
	canonical?: string;
	linked?: boolean;
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

/** A highlight joined with its screenshot's metadata, as /api/highlights returns. */
export interface HighlightWithMeta extends Highlight {
	book_title: string;
	sync_date: string;
	vault_png_path: string;
}

/** An excerpt saved on the device and synced through /api/v1/clippings. */
export interface Clipping {
	id: number;
	revision: number;
	document: string;
	clip_id: string;
	deleted: number;
	spine: number;
	start_page: number;
	end_page: number;
	pages: number;
	start_word: number;
	end_word: number;
	words: number;
	para: number | null;
	chapter: string;
	text: string;
	created_at: number;
	layout_signature: number;
	start_offset: number | null;
	end_offset: number | null;
	synced_at: string;
	book_title: string;
	screenshot_id: number | null;
	/** Only /api/clippings carries this; the per-book endpoint omits it. */
	kind?: 'book' | 'article';
}

export type SearchKind = 'screenshot' | 'note' | 'highlight' | 'clipping' | 'writing';

/** One indexed passage. `snippet` carries SQLite's <mark> tags and is rendered as HTML. */
export interface SearchHit {
	kind: SearchKind;
	ref_id: number;
	screenshot_id: number | null;
	book_title: string;
	book_slug: string;
	date: string;
	snippet: string;
	score: number;
}

export interface SearchResponse {
	q: string;
	kinds: SearchKind[];
	book: string;
	limit: number;
	offset: number;
	total: number;
	facets: Record<SearchKind, number>;
	results: SearchHit[];
}

export interface SearchReindex {
	indexed: Record<SearchKind, number>;
	skipped: SearchKind[];
	duration_ms: number;
}

/** Every destructive route answers with a token the toast can replay. */
export interface DeleteResponse {
	deleted: number;
	soft: boolean;
	writing_refs_marked: number;
	undo_token: string | null;
}

export interface HighlightDeleteResponse extends DeleteResponse {
	unmarked_vault: boolean;
}

export interface ClippingDeleteResponse extends DeleteResponse {
	revision: number;
}

export interface PurgeResponse {
	purged: number;
	row_retained: boolean;
	undo_token: null;
	reason?: string;
}

export interface TrashedHighlight {
	id: number;
	screenshot_id: number;
	selected_text: string;
	book_title: string;
	book_slug?: string;
	sync_date: string;
	deleted_at: string | null;
}

export interface TrashedClipping {
	id: number;
	clip_id: string;
	document: string;
	book_title: string;
	book_slug: string;
	chapter: string;
	text: string;
	deleted_at: string | null;
	deleted_source: string;
	/** False for tombstones written before soft delete landed: their text is gone. */
	restorable: boolean;
}

export interface TrashedWritingEntry {
	id: number;
	book_title: string;
	book_slug: string;
	title: string;
	entry_date: string;
	excerpt: string;
	ref_count: number;
	deleted_at: string | null;
	restorable: boolean;
}

export interface TrashResponse {
	counts: { highlights: number; clippings: number; writing: number };
	highlights: TrashedHighlight[];
	clippings: TrashedClipping[];
	writing: TrashedWritingEntry[];
}

export interface UndoEntry {
	token: string;
	kind: string;
	/** The complete toast line. Never derive copy from `kind`. */
	label: string;
	created_at: number;
	expires_at: number;
}

export interface UndoRunResponse {
	ok: boolean;
	already_undone: boolean;
	kind: string;
	label: string;
	result: Record<string, unknown>;
}

export type ExportFormat = 'md' | 'json' | 'csv';
export type ExportInclude = 'highlights' | 'clippings' | 'notes' | 'writing';

/** One citation inside a writing entry, carried verbatim in the JSON export. */
export interface ExportRef {
	anchor: string;
	id: number;
	kind: string;
	ref_id: number;
	document: string | null;
	snapshot: string;
	image_path: string | null;
	position: string;
	source_state: string;
}

export interface ExportItem {
	kind: 'highlight' | 'clipping' | 'note' | 'writing';
	id: number;
	date: string;
	text?: string;
	chapter?: string;
	page_label?: string;
	note?: string | null;
	screenshot_id?: number | null;
	/** Writing entries only. `body` keeps its anchors; `body_expanded` resolves them. */
	title?: string;
	body?: string;
	body_expanded?: string;
	position_pct?: number | null;
	refs?: ExportRef[];
}

export interface ExportGroup {
	label: string;
	items: ExportItem[];
}

export interface BookExport {
	book_slug: string;
	book_title: string;
	generated_at: string;
	include: ExportInclude[];
	grouped_by: 'chapter' | 'date';
	counts: Record<ExportInclude, number>;
	groups: ExportGroup[];
}

/** A passage resurfaced by "on this day" or the review queue. */
export interface ResurfacedItem {
	kind: 'screenshot' | 'highlight' | 'clipping';
	ref_id: number;
	screenshot_id: number | null;
	book_title: string;
	book_slug: string;
	date: string;
	text: string;
}

export interface OnThisDayYear {
	year: number;
	years_ago: number;
	items: ResurfacedItem[];
}

export interface OnThisDayResponse {
	date: string;
	total: number;
	years: OnThisDayYear[];
}

export type ReviewStrategy = 'spaced' | 'random';

export interface ReviewItem extends ResurfacedItem {
	shown_count: number;
	last_shown: string | null;
	next_due: string;
}

export interface ReviewResponse {
	strategy: ReviewStrategy;
	limit: number;
	kinds: string[];
	total_candidates: number;
	items: ReviewItem[];
}

export interface ReviewSeenResponse {
	kind: string;
	ref_id: number;
	shown_count: number;
	interval_days: number;
	next_due: string;
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

function createApi(customFetch?: Fetch) {
	// Resolved per call, not captured at module load, so a caller (or a test)
	// can replace globalThis.fetch after this module has been imported.
	const send: Fetch = (...args) => (customFetch ?? globalThis.fetch)(...args);

	async function get<T>(url: string): Promise<T> {
		const res = await send(url);
		if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${url}`);
		return res.json() as Promise<T>;
	}

	async function put<T>(url: string, body: unknown): Promise<T> {
		const res = await send(url, {
			method: 'PUT',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(body),
		});
		if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${url}`);
		return res.json() as Promise<T>;
	}

	async function post<T>(url: string, body: unknown): Promise<T> {
		const res = await send(url, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(body),
		});
		if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${url}`);
		return res.json() as Promise<T>;
	}

	async function del<T>(url: string): Promise<T> {
		const res = await send(url, { method: 'DELETE' });
		if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${url}`);
		return res.json() as Promise<T>;
	}

	// A download is a plain anchor at this URL so the browser honours
	// Content-Disposition; only "copy as JSON" goes through fetch.
	function exportUrl(
		slug: string,
		opts: { format: ExportFormat; include: ExportInclude[]; group?: string },
	) {
		const params = new URLSearchParams({
			format: opts.format,
			include: opts.include.join(','),
			group: opts.group ?? 'auto',
		});
		return `/api/books/${encodeURIComponent(slug)}/export?${params}`;
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
			highlights: (slug: string) =>
				get<HighlightWithMeta[]>(`/api/books/${encodeURIComponent(slug)}/highlights`),
			clippings: (slug: string) =>
				get<Clipping[]>(`/api/books/${encodeURIComponent(slug)}/clippings`),
			exportUrl,
			exportJson: (slug: string, include: ExportInclude[]) =>
				get<BookExport>(exportUrl(slug, { format: 'json', include })),
		},

		articles: {
			list: () => get<Article[]>('/api/articles'),
			get: (slug: string) => get<ArticleDetail>(`/api/articles/${encodeURIComponent(slug)}`),
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
				del<HighlightDeleteResponse>(`/api/highlights/${id}`),
			restore: (id: number) =>
				post<Highlight>(`/api/highlights/${id}/restore`, {}),
		},

		clippings: {
			listAll: (limit = 200) => get<Clipping[]>(`/api/clippings?limit=${limit}`),
			delete: (id: number) => del<ClippingDeleteResponse>(`/api/clippings/${id}`),
			restore: (id: number) =>
				post<{ restored: number; revision: number; writing_refs_restored: number }>(
					`/api/clippings/${id}/restore`,
					{},
				),
		},

		trash: {
			get: () => get<TrashResponse>('/api/trash'),
			restoreHighlight: (id: number) => post<Highlight>(`/api/highlights/${id}/restore`, {}),
			restoreClipping: (id: number) =>
				post<{ restored: number; revision: number }>(`/api/clippings/${id}/restore`, {}),
			purgeHighlight: (id: number) =>
				del<PurgeResponse>(`/api/trash/highlights/${id}?confirm=1`),
			purgeClipping: (id: number) =>
				del<PurgeResponse>(`/api/trash/clippings/${id}?confirm=1`),
			empty: () =>
				post<{ purged: Record<string, number>; rows_retained: number }>(
					'/api/trash/empty?confirm=1',
					{},
				),
		},

		undo: {
			run: (token: string) => post<UndoRunResponse>('/api/undo', { token }),
			recent: (limit = 5) => get<{ items: UndoEntry[] }>(`/api/undo/recent?limit=${limit}`),
		},

		search: (
			q: string,
			opts: { kinds?: SearchKind[]; book?: string; limit?: number; offset?: number } = {},
		) => {
			const params = new URLSearchParams();
			if (q.trim()) params.set('q', q);
			if (opts.kinds?.length) params.set('kinds', opts.kinds.join(','));
			if (opts.book) params.set('book', opts.book);
			params.set('limit', String(opts.limit ?? 50));
			params.set('offset', String(opts.offset ?? 0));
			return get<SearchResponse>(`/api/search?${params}`);
		},

		reindexSearch: () => post<SearchReindex>('/api/search/reindex', {}),

		onThisDay: (date?: string) =>
			get<OnThisDayResponse>(
				`/api/on-this-day${date ? `?date=${encodeURIComponent(date)}` : ''}`,
			),

		review: (
			opts: {
				strategy?: ReviewStrategy;
				limit?: number;
				kinds?: string[];
				seed?: string;
			} = {},
		) => {
			const params = new URLSearchParams({
				strategy: opts.strategy ?? 'spaced',
				limit: String(opts.limit ?? 10),
			});
			if (opts.kinds?.length) params.set('kinds', opts.kinds.join(','));
			if (opts.seed) params.set('seed', opts.seed);
			return get<ReviewResponse>(`/api/review?${params}`);
		},

		reviewSeen: (kind: string, refId: number, rating: 'good' | 'again') =>
			post<ReviewSeenResponse>(`/api/review/${kind}/${refId}/seen`, { rating }),

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
			listUnresolved: () => get<UnresolvedAlias[]>('/api/aliases/unresolved'),
			set: (hash: string, title: string, filename = '') =>
				put<Alias>(`/api/aliases/${hash}`, { title, filename }),
		},

		devices: {
			list: () => get<SyncDevice[]>('/api/devices'),
		},
	};
}

export { createApi };
export const api = createApi();
