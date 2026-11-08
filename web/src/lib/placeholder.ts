/**
 * Deterministic clothbound cover geometry and palette, derived from a book slug.
 *
 * Pure module: no Svelte import, no network, no dependency. The same slug always
 * produces the same cover, in the browser and in a unit test, which is what lets
 * this double as the fallback for a real cover that is missing or fails to load.
 */

export interface CoverPalette {
	/** Flat field colour of the binding. */
	cloth: string;
	/** Title and author text. */
	ink: string;
	/** Horizontal rules and the inset frame. */
	rule: string;
}

export interface CoverStyle {
	palette: CoverPalette;
	/** Hue in degrees: the only per-book axis of the palette. */
	hue: number;
	/** 2 to 4 horizontal rules in the upper third. */
	ruleCount: number;
	/** 0..1, where the rule block sits inside its band. */
	ruleOffset: number;
	/** 0..1, strength of the cloth grain. */
	grain: number;
	/** feTurbulence seed, and the suffix of the filter element id. */
	seed: number;
}

export const COVER_WIDTH = 120;
export const COVER_HEIGHT = 180;
export const COVER_MARGIN = 14;
export const COVER_INNER_WIDTH = COVER_WIDTH - COVER_MARGIN * 2;

export const TITLE_FONT_SIZE = 11;
export const TITLE_MAX_CHARS = 15;
export const TITLE_MAX_LINES = 4;
export const AUTHOR_FONT_SIZE = 7;
export const AUTHOR_MAX_CHARS = 26;

/** 18 hues, 20 degrees apart, so two adjacent covers are never near-identical. */
const HUE_STEPS = 18;

/** FNV-1a 32-bit. Six lines, no dependency, good avalanche on short strings. */
export function hashSlug(slug: string): number {
	let h = 0x811c9dc5;
	for (let i = 0; i < slug.length; i++) {
		const c = slug.charCodeAt(i);
		h = Math.imul(h ^ (c & 0xff), 0x01000193);
		h = Math.imul(h ^ (c >>> 8), 0x01000193);
	}
	return h >>> 0;
}

/**
 * Lightness and chroma come from the theme tokens in app.css, so a cover follows
 * dark/light mode and can never sit outside the interface's own range. Only the
 * hue rotates per book, which is what makes a shelf look like a set of books
 * rather than a set of unrelated swatches.
 */
export function paletteForHue(hue: number): CoverPalette {
	return {
		cloth: `oklch(var(--l-sunken) calc(var(--accent-c) * 0.55) ${hue})`,
		ink: `oklch(var(--l-text) calc(var(--accent-c) * 0.12) ${hue})`,
		rule: `oklch(var(--l-accent) calc(var(--accent-c) * 0.7) ${hue})`,
	};
}

/** Each field reads a different byte of the hash, so one changed character moves all of them. */
export function coverStyle(slug: string): CoverStyle {
	const h = hashSlug(slug ?? '');
	const hue = ((h & 0xff) % HUE_STEPS) * (360 / HUE_STEPS);
	return {
		palette: paletteForHue(hue),
		hue,
		ruleCount: 2 + (((h >>> 8) & 0xff) % 3),
		ruleOffset: ((h >>> 16) & 0xff) / 255,
		grain: ((h >>> 24) & 0xff) / 255,
		seed: h,
	};
}

/** Break after a hyphen or slash: most book folders here are hyphen-slugged titles. */
const BREAK_AFTER = /(?<=[-\u2013\u2014/_])/;

interface Chunk {
	text: string;
	/** True when the chunk continues the previous one, so no space is inserted. */
	glue: boolean;
}

function chunkWords(text: string, maxChars: number): Chunk[] {
	const chunks: Chunk[] = [];
	for (const word of text.trim().split(/\s+/)) {
		if (!word) continue;
		let first = true;
		for (const piece of word.split(BREAK_AFTER)) {
			if (!piece) continue;
			// Anything still wider than the whole line is hard-broken rather than left to overflow.
			for (let i = 0; i < piece.length; i += maxChars) {
				chunks.push({ text: piece.slice(i, i + maxChars), glue: !first });
				first = false;
			}
		}
	}
	return chunks;
}

/**
 * Wrap `text` into at most `maxLines` lines of at most `maxChars` characters,
 * ellipsising the last line when something was dropped. SVG text does not wrap
 * or ellipsise on its own, so this has to happen before render.
 */
export function wrapText(text: string, maxChars: number, maxLines: number): string[] {
	if (!text || maxChars < 1 || maxLines < 1) return [];

	const chunks = chunkWords(text, maxChars);
	if (chunks.length === 0) return [];

	const lines: string[] = [];
	let line = '';
	for (const chunk of chunks) {
		const next = line ? `${line}${chunk.glue ? '' : ' '}${chunk.text}` : chunk.text;
		if (next.length <= maxChars) {
			line = next;
		} else {
			lines.push(line);
			line = chunk.text;
		}
	}
	if (line) lines.push(line);
	if (lines.length <= maxLines) return lines;

	const kept = lines.slice(0, maxLines);
	const last = kept[maxLines - 1];
	kept[maxLines - 1] = `${last.slice(0, Math.max(0, maxChars - 1)).trimEnd()}\u2026`;
	return kept;
}

const WIDE = /[A-Z0-9@#&%MW]/;
const NARROW = /[ijlt.,:;'!|()[\]]/;

/**
 * Rough advance width of a Georgia-like serif. Only accurate enough to decide
 * when a line needs clamping, which is all the caller does with it.
 */
export function estimateTextWidth(text: string, fontSize: number): number {
	let em = 0;
	for (const ch of text) {
		if (ch === ' ') em += 0.26;
		else if (WIDE.test(ch)) em += 0.7;
		else if (NARROW.test(ch)) em += 0.3;
		else em += 0.53;
	}
	return em * fontSize;
}
