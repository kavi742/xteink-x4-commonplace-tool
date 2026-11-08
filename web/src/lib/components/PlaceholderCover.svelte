<script lang="ts">
	import {
		coverStyle,
		wrapText,
		estimateTextWidth,
		COVER_WIDTH,
		COVER_HEIGHT,
		COVER_MARGIN,
		COVER_INNER_WIDTH,
		TITLE_FONT_SIZE,
		TITLE_MAX_CHARS,
		TITLE_MAX_LINES,
		AUTHOR_FONT_SIZE,
		AUTHOR_MAX_CHARS,
	} from '$lib/placeholder';

	type CoverSize = 'sm' | 'md' | 'fill';

	let {
		slug,
		title = '',
		author = '',
		size = 'md',
	}: {
		slug: string;
		title?: string;
		author?: string;
		size?: CoverSize;
	} = $props();

	/** Rendered width in px. `fill` hands the decision to the container. */
	const WIDTH_PX: Record<CoverSize, number | null> = { sm: 16, md: 60, fill: null };

	const TITLE_LEADING = 13;
	const RULE_GAP = 4.5;
	const RULE_BAND_TOP = 24;
	const RULE_BAND_TRAVEL = 14;
	/** The hash gives grain as 0..1; anything above a few percent stops reading as cloth. */
	const GRAIN_MAX = 0.07;

	let cover = $derived(coverStyle(slug ?? ''));
	// At 16px wide the title would render under 3px tall, so the chip carries colour only.
	let compact = $derived(size === 'sm');

	let titleLines = $derived(
		(compact ? [] : wrapText(title, TITLE_MAX_CHARS, TITLE_MAX_LINES)).map((text) => ({
			text,
			clamped: estimateTextWidth(text, TITLE_FONT_SIZE) > COVER_INNER_WIDTH,
		}))
	);
	let authorLine = $derived(compact ? '' : (wrapText(author, AUTHOR_MAX_CHARS, 1)[0] ?? ''));

	let titleTop = $derived(100 - ((titleLines.length - 1) * TITLE_LEADING) / 2);
	let authorY = $derived(titleTop + titleLines.length * TITLE_LEADING + 2);
	let ruleYs = $derived(
		Array.from(
			{ length: cover.ruleCount },
			(_, i) => RULE_BAND_TOP + cover.ruleOffset * RULE_BAND_TRAVEL + i * RULE_GAP
		)
	);

	let widthPx = $derived(WIDTH_PX[size]);
	let filterId = $derived(`pc-grain-${cover.seed}`);
	let label = $derived(`Placeholder cover for ${title || slug || 'an untitled book'}`);
</script>

<svg
	class="placeholder-cover"
	class:fill={size === 'fill'}
	width={widthPx ?? undefined}
	height={widthPx === null ? undefined : widthPx * (COVER_HEIGHT / COVER_WIDTH)}
	viewBox="0 0 {COVER_WIDTH} {COVER_HEIGHT}"
	role="img"
	aria-label={label}
	style="--cv-cloth:{cover.palette.cloth};--cv-ink:{cover.palette.ink};--cv-rule:{cover.palette
		.rule}"
>
	<rect class="cloth" width={COVER_WIDTH} height={COVER_HEIGHT} />

	{#if !compact}
		<defs>
			<filter id={filterId} x="0" y="0" width="100%" height="100%">
				<feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="1" seed={cover.seed} />
				<feColorMatrix type="saturate" values="0" />
			</filter>
		</defs>
		<rect
			class="grain"
			width={COVER_WIDTH}
			height={COVER_HEIGHT}
			filter="url(#{filterId})"
			opacity={cover.grain * GRAIN_MAX}
		/>
	{/if}

	<rect class="frame" x="6" y="6" width={COVER_WIDTH - 12} height={COVER_HEIGHT - 12} />

	{#each ruleYs as y}
		<line class="rule" x1={COVER_MARGIN} x2={COVER_WIDTH - COVER_MARGIN} y1={y} y2={y} />
	{/each}

	{#each titleLines as line, i}
		<text
			class="title"
			x={COVER_WIDTH / 2}
			y={titleTop + i * TITLE_LEADING}
			text-anchor="middle"
			font-size={TITLE_FONT_SIZE}
			textLength={line.clamped ? COVER_INNER_WIDTH : undefined}
			lengthAdjust={line.clamped ? 'spacingAndGlyphs' : undefined}>{line.text}</text
		>
	{/each}

	{#if authorLine}
		<text
			class="author"
			x={COVER_WIDTH / 2}
			y={authorY}
			text-anchor="middle"
			font-size={AUTHOR_FONT_SIZE}>{authorLine}</text
		>
	{/if}
</svg>

<style>
	.placeholder-cover {
		display: block;
	}
	.placeholder-cover.fill {
		width: 100%;
		height: auto;
	}
	.cloth {
		fill: var(--cv-cloth);
	}
	.grain {
		fill: var(--cv-ink);
		pointer-events: none;
	}
	.frame {
		fill: none;
		stroke: var(--cv-rule);
		stroke-opacity: 0.45;
		stroke-width: 1;
	}
	.rule {
		stroke: var(--cv-rule);
		stroke-width: 1.2;
	}
	.title {
		fill: var(--cv-ink);
		font-family: var(--font-serif);
		font-weight: 600;
	}
	.author {
		fill: var(--cv-ink);
		fill-opacity: 0.72;
		font-family: var(--font-serif);
		font-style: italic;
	}
</style>
