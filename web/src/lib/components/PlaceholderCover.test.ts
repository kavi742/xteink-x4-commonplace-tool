/**
 * Tests for src/lib/placeholder.ts and src/lib/components/PlaceholderCover.svelte
 *
 * The cover is a pure function of its props, so everything here is assertable
 * without a network, a backend, or a real browser.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import PlaceholderCover from '$lib/components/PlaceholderCover.svelte';
import {
	hashSlug,
	coverStyle,
	wrapText,
	estimateTextWidth,
	COVER_WIDTH,
	COVER_HEIGHT,
	COVER_INNER_WIDTH,
	TITLE_FONT_SIZE,
	TITLE_MAX_CHARS,
	TITLE_MAX_LINES,
} from '$lib/placeholder';

const LONG_TITLE =
	'The Exceptionally Protracted and Altogether Unreasonable Chronicle of a Subtitle ' +
	'That Simply Refuses to Stop Announcing Itself to the Reader';

// ------------------------------------------------------------------ //
// hashSlug                                                             //
// ------------------------------------------------------------------ //

describe('hashSlug', () => {
	it('is deterministic', () => {
		expect(hashSlug('pastoral')).toBe(hashSlug('pastoral'));
		expect(hashSlug('')).toBe(hashSlug(''));
	});

	it('differs for a one-character change', () => {
		expect(hashSlug('the-hidden-keys')).not.toBe(hashSlug('the-hidden-key'));
	});

	it('stays an unsigned 32-bit integer', () => {
		for (const slug of ['', 'a', 'pastoral', 'życie-pi', '📚-a-book', 'x'.repeat(500)]) {
			const h = hashSlug(slug);
			expect(Number.isInteger(h)).toBe(true);
			expect(h).toBeGreaterThanOrEqual(0);
			expect(h).toBeLessThanOrEqual(0xffffffff);
		}
	});
});

// ------------------------------------------------------------------ //
// coverStyle                                                           //
// ------------------------------------------------------------------ //

describe('coverStyle', () => {
	it('is deterministic', () => {
		expect(coverStyle('pastoral')).toEqual(coverStyle('pastoral'));
	});

	it('keeps every derived field in range over many slugs', () => {
		for (let i = 0; i < 200; i++) {
			const style = coverStyle(`book-${i}`);
			expect(style.ruleCount).toBeGreaterThanOrEqual(2);
			expect(style.ruleCount).toBeLessThanOrEqual(4);
			expect(style.ruleOffset).toBeGreaterThanOrEqual(0);
			expect(style.ruleOffset).toBeLessThanOrEqual(1);
			expect(style.grain).toBeGreaterThanOrEqual(0);
			expect(style.grain).toBeLessThanOrEqual(1);
			expect(style.hue).toBeGreaterThanOrEqual(0);
			expect(style.hue).toBeLessThan(360);
		}
	});

	it('gives two different slugs different palettes', () => {
		const pairs: [string, string][] = [
			['pastoral', 'life-of-pi'],
			['the-hidden-keys', 'the-hidden-key'],
			['articles', 'son-of-nobody'],
		];
		for (const [a, b] of pairs) {
			expect(coverStyle(a).palette).not.toEqual(coverStyle(b).palette);
		}
	});

	it('spreads a library across many hues', () => {
		const hues = new Set(Array.from({ length: 60 }, (_, i) => coverStyle(`book-${i}`).hue));
		expect(hues.size).toBeGreaterThanOrEqual(8);
	});

	it('derives colour from the theme tokens rather than literal values', () => {
		const { palette } = coverStyle('pastoral');
		for (const colour of [palette.cloth, palette.ink, palette.rule]) {
			expect(colour).toContain('oklch(');
			expect(colour).toContain('var(--');
			expect(colour).not.toMatch(/#[0-9a-f]{3,8}/i);
		}
	});

	it('does not throw on an empty or missing slug', () => {
		expect(() => coverStyle('')).not.toThrow();
		expect(() => coverStyle(undefined as unknown as string)).not.toThrow();
	});
});

// ------------------------------------------------------------------ //
// wrapText                                                             //
// ------------------------------------------------------------------ //

describe('wrapText', () => {
	it('bounds both line length and line count', () => {
		const lines = wrapText(LONG_TITLE, TITLE_MAX_CHARS, TITLE_MAX_LINES);
		expect(lines.length).toBeLessThanOrEqual(TITLE_MAX_LINES);
		for (const line of lines) expect(line.length).toBeLessThanOrEqual(TITLE_MAX_CHARS);
	});

	it('ellipsises only when something was dropped', () => {
		expect(wrapText(LONG_TITLE, TITLE_MAX_CHARS, TITLE_MAX_LINES).at(-1)).toMatch(/\u2026$/);
		expect(wrapText('Pastoral', TITLE_MAX_CHARS, TITLE_MAX_LINES)).toEqual(['Pastoral']);
	});

	it('hard-breaks a word wider than the line', () => {
		const lines = wrapText('Donaudampfschifffahrtsgesellschaft', 10, 4);
		for (const line of lines) expect(line.length).toBeLessThanOrEqual(10);
		expect(lines.join('').replace(/\u2026/g, '')).toMatch(/^Donaudampf/);
	});

	it('breaks a hyphen-slugged title after a hyphen', () => {
		expect(wrapText('The-Bhagavad-Gita', TITLE_MAX_CHARS, TITLE_MAX_LINES)).toEqual([
			'The-Bhagavad-',
			'Gita',
		]);
		expect(wrapText('A-Short-Stay-in-Hell', TITLE_MAX_CHARS, TITLE_MAX_LINES)).toEqual([
			'A-Short-Stay-',
			'in-Hell',
		]);
	});

	it('returns nothing for empty input', () => {
		expect(wrapText('', TITLE_MAX_CHARS, TITLE_MAX_LINES)).toEqual([]);
		expect(wrapText('   ', TITLE_MAX_CHARS, TITLE_MAX_LINES)).toEqual([]);
	});
});

// ------------------------------------------------------------------ //
// PlaceholderCover                                                     //
// ------------------------------------------------------------------ //

function cover(props: Record<string, unknown>) {
	const { container } = render(PlaceholderCover, { props: props as never });
	const svg = container.querySelector('svg');
	if (!svg) throw new Error('no <svg> rendered');
	return { container, svg };
}

describe('PlaceholderCover', () => {
	it('renders the same markup twice for the same slug', () => {
		const first = cover({ slug: 'pastoral', title: 'Pastoral' });
		const second = cover({ slug: 'pastoral', title: 'Pastoral' });
		expect(first.container.innerHTML).toBe(second.container.innerHTML);
	});

	it('renders different markup for different slugs', () => {
		const a = cover({ slug: 'pastoral', title: 'Pastoral' });
		const b = cover({ slug: 'life-of-pi', title: 'Pastoral' });
		expect(a.container.innerHTML).not.toBe(b.container.innerHTML);
	});

	it('exposes an image role whose accessible name carries the title', () => {
		cover({ slug: 'pastoral', title: 'Pastoral' });
		const svg = screen.getByRole('img', { name: /Pastoral/ });
		expect(svg.tagName.toLowerCase()).toBe('svg');
		expect(svg.getAttribute('aria-label')).toContain('Pastoral');
	});

	it('falls back to the slug in the accessible name when there is no title', () => {
		const { svg } = cover({ slug: 'son-of-nobody' });
		expect(svg.getAttribute('aria-label')).toContain('son-of-nobody');
	});

	it('renders without throwing for an empty or missing slug', () => {
		expect(() => cover({ slug: '' })).not.toThrow();
		expect(() => cover({ slug: undefined, title: 'Untitled' })).not.toThrow();
		const { svg } = cover({ slug: '' });
		expect(svg.getAttribute('aria-label')).toBeTruthy();
		expect(svg.querySelectorAll('rect').length).toBeGreaterThan(0);
	});

	it('keeps a very long title inside the viewBox', () => {
		const { svg } = cover({ slug: 'pastoral', title: LONG_TITLE });
		expect(svg.getAttribute('viewBox')).toBe(`0 0 ${COVER_WIDTH} ${COVER_HEIGHT}`);

		const texts = Array.from(svg.querySelectorAll('text'));
		expect(texts.length).toBeGreaterThan(0);
		expect(texts.length).toBeLessThanOrEqual(TITLE_MAX_LINES);

		for (const text of texts) {
			const content = text.textContent ?? '';
			expect(content.length).toBeLessThanOrEqual(TITLE_MAX_CHARS);

			const x = Number(text.getAttribute('x'));
			const y = Number(text.getAttribute('y'));
			expect(x).toBeGreaterThanOrEqual(0);
			expect(x).toBeLessThanOrEqual(COVER_WIDTH);
			expect(y).toBeGreaterThanOrEqual(0);
			expect(y).toBeLessThanOrEqual(COVER_HEIGHT);

			// Either the line already fits, or it is clamped to the inner width.
			const fits = estimateTextWidth(content, TITLE_FONT_SIZE) <= COVER_INNER_WIDTH;
			expect(fits || text.getAttribute('textLength') === String(COVER_INNER_WIDTH)).toBe(true);
		}
	});

	it('sizes itself from the size prop and drops text at chip size', () => {
		const small = cover({ slug: 'pastoral', title: 'Pastoral', size: 'sm' });
		expect(small.svg.querySelectorAll('text')).toHaveLength(0);
		expect(small.svg.getAttribute('width')).toBe('16');

		const medium = cover({ slug: 'pastoral', title: 'Pastoral', size: 'md' });
		expect(medium.svg.getAttribute('width')).toBe('60');
		expect(medium.svg.querySelectorAll('text').length).toBeGreaterThan(0);

		const filled = cover({ slug: 'pastoral', title: 'Pastoral', size: 'fill' });
		expect(filled.svg.getAttribute('width')).toBeNull();
		expect(filled.svg.classList.contains('fill')).toBe(true);
	});

	it('shows the author when one is given', () => {
		const { svg } = cover({ slug: 'pastoral', title: 'Pastoral', author: 'Nescio' });
		const contents = Array.from(svg.querySelectorAll('text')).map((t) => t.textContent);
		expect(contents).toContain('Nescio');
	});
});
