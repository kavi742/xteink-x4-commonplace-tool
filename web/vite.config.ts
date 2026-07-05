import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig(({ mode }) => ({
	plugins: [
		sveltekit({
			compilerOptions: {
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			},
			adapter: adapter({
				pages: 'build',
				assets: 'build',
				fallback: 'index.html',
			})
		})
	],
	// In dev, proxy /api and /status to the FastAPI backend on :8090
	server: {
		proxy: {
			'/api': 'http://localhost:8090',
			'/status': 'http://localhost:8090',
			'/syncs': 'http://localhost:8090',
		}
	},
	test: {
		environment: 'jsdom',
		setupFiles: ['./src/test/setup.ts'],
		include: ['src/**/*.test.ts'],
		globals: true,
	}
}));
