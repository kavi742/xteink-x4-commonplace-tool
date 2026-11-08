import { api } from '$lib/api';
import type { ReviewStrategy } from '$lib/api';

export async function load({ url }) {
	const strategy: ReviewStrategy = url.searchParams.get('strategy') === 'random' ? 'random' : 'spaced';
	const queue = await api.review({ strategy, limit: 20 }).catch(() => null);
	return { strategy, queue };
}
