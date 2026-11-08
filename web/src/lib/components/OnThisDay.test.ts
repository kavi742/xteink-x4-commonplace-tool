/**
 * Tests for the "on this day" block.
 *
 * A young archive has no prior years, so rendering nothing at all is the
 * correct output rather than an empty heading.
 */
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import OnThisDay from './OnThisDay.svelte';
import type { OnThisDayResponse, ResurfacedItem } from '$lib/api';

function item(over: Partial<ResurfacedItem> = {}): ResurfacedItem {
	return {
		kind: 'screenshot',
		ref_id: 112,
		screenshot_id: 112,
		book_title: 'Pastoral',
		book_slug: 'pastoral',
		date: '2025-11-07',
		text: 'The pastoral mode of attention.',
		...over,
	};
}

function response(over: Partial<OnThisDayResponse> = {}): OnThisDayResponse {
	return {
		date: '2026-11-07',
		total: 1,
		years: [{ year: 2025, years_ago: 1, items: [item()] }],
		...over,
	};
}

afterEach(cleanup);

describe('OnThisDay', () => {
	it('renders nothing when total is 0', () => {
		const { container } = render(OnThisDay, {
			props: { data: response({ total: 0, years: [] }) },
		});
		expect(container.textContent?.trim()).toBe('');
	});

	it('renders one section per year with a correct caption', () => {
		render(OnThisDay, {
			props: {
				data: response({
					total: 2,
					years: [
						{ year: 2025, years_ago: 1, items: [item()] },
						{
							year: 2024,
							years_ago: 2,
							items: [item({ ref_id: 9, text: 'Two years back.', date: '2024-11-07' })],
						},
					],
				}),
			},
		});

		expect(screen.getByText('1 year ago')).toBeTruthy();
		expect(screen.getByText('2 years ago')).toBeTruthy();
		expect(screen.getByText('The pastoral mode of attention.')).toBeTruthy();
		expect(screen.getByText('Two years back.')).toBeTruthy();
	});

	it('links a clipping to its book rather than opening a screenshot panel', () => {
		render(OnThisDay, {
			props: {
				data: response({
					years: [
						{
							year: 2025,
							years_ago: 1,
							items: [item({ kind: 'clipping', screenshot_id: null })],
						},
					],
				}),
			},
		});

		const link = screen.getByRole('link');
		expect(link.getAttribute('href')).toBe('/books/Pastoral');
	});

	it('offers a screenshot-backed passage as a button', () => {
		render(OnThisDay, { props: { data: response() } });
		expect(screen.getByRole('button')).toBeTruthy();
	});
});
