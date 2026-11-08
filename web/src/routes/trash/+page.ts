import { api } from '$lib/api';
import type { TrashResponse } from '$lib/api';

const EMPTY: TrashResponse = {
	counts: { highlights: 0, clippings: 0, writing: 0 },
	highlights: [],
	clippings: [],
	writing: [],
};

export async function load() {
	const trash = await api.trash.get().catch(() => EMPTY);
	return { trash };
}
