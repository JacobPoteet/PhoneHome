import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

// The web app is a single-page app: built to static files that the
// PhoneHome server hosts (no Node in production). `npm run dev` proxies
// the API to a server running on :8080.
const server = process.env.PHONEHOME_SERVER || 'http://127.0.0.1:8080';

export default defineConfig({
	plugins: [
		sveltekit({
			adapter: adapter({ fallback: 'index.html', precompress: false }),
			version: { pollInterval: 0 }
		})
	],
	server: {
		proxy: {
			'/api': server,
			'/media': server,
			'/health': server
		}
	}
});
