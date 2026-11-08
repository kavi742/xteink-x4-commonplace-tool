/**
 * Tests for the export popover.
 *
 * The href is the whole contract: a download is a plain anchor, so whatever
 * the checkboxes say has to be in the query string before the click happens.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/svelte';
import ExportMenu from './ExportMenu.svelte';
import { api } from '$lib/api';
import type { ExportInclude } from '$lib/api';

type MenuProps = { slug: string; defaultInclude?: ExportInclude[] };

function hrefFor(label: string) {
	return (screen.getByText(label) as HTMLAnchorElement).getAttribute('href') ?? '';
}

async function open(props: MenuProps = { slug: 'Pastoral' }) {
	render(ExportMenu, { props });
	await fireEvent.click(screen.getByRole('button', { name: 'Export' }));
	return screen.getByRole('dialog');
}

beforeEach(() => vi.restoreAllMocks());
afterEach(() => {
	cleanup();
	vi.restoreAllMocks();
});

describe('ExportMenu', () => {
	it('stays closed until the trigger is clicked', () => {
		render(ExportMenu, { props: { slug: 'Pastoral' } });
		expect(screen.queryByRole('dialog')).toBeNull();
	});

	it('opens on click and closes on a second click', async () => {
		await open();
		await fireEvent.click(screen.getByRole('button', { name: 'Export' }));
		expect(screen.queryByRole('dialog')).toBeNull();
	});

	it('closes on Escape', async () => {
		await open();
		await fireEvent.keyDown(document, { key: 'Escape' });
		await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
	});

	it('closes on an outside click', async () => {
		await open();
		await fireEvent.click(document.body);
		await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
	});

	it('offers all three formats, each pointing at the export endpoint', async () => {
		await open();
		for (const label of ['Markdown', 'CSV', 'JSON']) {
			expect(hrefFor(label)).toContain('/api/books/Pastoral/export?');
		}
		expect(hrefFor('Markdown')).toContain('format=md');
		expect(hrefFor('CSV')).toContain('format=csv');
		expect(hrefFor('JSON')).toContain('format=json');
	});

	it('joins include with commas rather than repeating the param', async () => {
		await open();
		const href = hrefFor('Markdown');
		expect(href).toContain('include=highlights%2Cclippings%2Cnotes%2Cwriting');
		expect(href.match(/include=/g)).toHaveLength(1);
	});

	it('drops an unchecked kind from the generated href', async () => {
		await open();
		await fireEvent.click(screen.getByLabelText('Clippings'));

		const href = hrefFor('Markdown');
		expect(href).not.toContain('clippings');
		expect(href).toContain('highlights');
	});

	it('honours defaultInclude', async () => {
		await open({ slug: 'Pastoral', defaultInclude: ['clippings'] });
		expect(hrefFor('Markdown')).toContain('include=clippings');
	});

	it('falls back to the default set when nothing is ticked', async () => {
		await open({ slug: 'Pastoral', defaultInclude: ['clippings'] });
		await fireEvent.click(screen.getByLabelText('Clippings'));

		expect(screen.getByText('Nothing ticked, so the default set is exported.')).toBeTruthy();
		expect(hrefFor('Markdown')).toContain('include=clippings');
	});

	it('URL-encodes a slug with a space', async () => {
		await open({ slug: 'Fifteen Dogs' });
		expect(hrefFor('Markdown')).toContain('/api/books/Fifteen%20Dogs/export?');
	});

	it('copies the JSON export to the clipboard', async () => {
		const payload = { book_slug: 'pastoral', counts: { highlights: 1 } };
		const exportJson = vi.spyOn(api.books, 'exportJson').mockResolvedValue(payload as never);
		const writeText = vi.fn(async (_text: string) => {});
		Object.assign(navigator, { clipboard: { writeText } });

		await open();
		await fireEvent.click(screen.getByRole('button', { name: 'Copy JSON' }));

		await waitFor(() => expect(writeText).toHaveBeenCalledOnce());
		expect(exportJson).toHaveBeenCalledWith('Pastoral', [
			'highlights',
			'clippings',
			'notes',
			'writing',
		]);
		expect(writeText.mock.calls[0][0]).toContain('"book_slug"');
		await waitFor(() => expect(screen.getByText('Copied')).toBeTruthy());
	});

	it('reports a failed copy rather than throwing', async () => {
		vi.spyOn(api.books, 'exportJson').mockRejectedValue(new Error('500'));

		await open();
		await fireEvent.click(screen.getByRole('button', { name: 'Copy JSON' }));

		await waitFor(() => expect(screen.getByText('Copy failed')).toBeTruthy());
	});
});
