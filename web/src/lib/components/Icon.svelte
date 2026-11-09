<script>
	// Small inline icons (24×24, stroke = currentColor).
	let { name, size = 20, label = '' } = $props();
	const paths = {
		messages: 'M4 5h16v11H8l-4 4V5z',
		search: 'M11 4a7 7 0 1 1 0 14 7 7 0 0 1 0-14zm5 12 4 4',
		calendar: 'M4 6h16v14H4zM4 10h16M9 3v4M15 3v4',
		greeting: 'M4 10v4h3l5 4V6L7 10H4zm12-1a4 4 0 0 1 0 6m2-9a8 8 0 0 1 0 12',
		phone: 'M7 3h3l2 5-2.5 1.5a11 11 0 0 0 5 5L16 12l5 2v3a3 3 0 0 1-3 3A15 15 0 0 1 4 6a3 3 0 0 1 3-3z',
		settings: 'M12 9a3 3 0 1 1 0 6 3 3 0 0 1 0-6zm7.4 4.5 1.6 1.2-2 3.4-1.9-.8a7 7 0 0 1-2 1.2L14.8 21h-4l-.3-2a7 7 0 0 1-2-1.2l-1.9.8-2-3.4 1.6-1.2a7 7 0 0 1 0-2.4L4.6 10.4l2-3.4 1.9.8a7 7 0 0 1 2-1.2l.3-2h4l.3 2a7 7 0 0 1 2 1.2l1.9-.8 2 3.4-1.6 1.2a7 7 0 0 1 0 2.4z',
		play: 'M8 5v14l11-7z',
		pause: 'M7 5h4v14H7zM13 5h4v14h-4z',
		star: 'M12 3l2.8 5.7 6.2.9-4.5 4.4 1 6.2L12 17.3 6.5 20.2l1-6.2L3 9.6l6.2-.9z',
		trash: 'M5 7h14M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3',
		mic: 'M12 3a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3zM6 11a6 6 0 0 0 12 0M12 17v4',
		upload: 'M12 16V4m0 0-4 4m4-4 4 4M4 16v4h16v-4',
		download: 'M12 4v12m0 0-4-4m4 4 4-4M4 16v4h16v-4',
		x: 'M6 6l12 12M18 6 6 18',
		check: 'M5 12l5 5 9-10',
		back: 'M15 5l-7 7 7 7',
		refresh: 'M20 11a8 8 0 1 0-2.3 5.7M20 5v6h-6',
		edit: 'M4 20h4L19 9l-4-4L4 16v4zM13 7l4 4',
		stop: 'M7 7h10v10H7z',
		bell: 'M6 16V11a6 6 0 1 1 12 0v5l2 2H4l2-2zM10 20a2 2 0 0 0 4 0',
		heart: 'M12 20s-7-4.5-7-10a4 4 0 0 1 7-2.5A4 4 0 0 1 19 10c0 5.5-7 10-7 10z',
		chevron: 'M9 6l6 6-6 6'
	};
	const filled = new Set(['play', 'pause', 'stop']);
</script>

<svg
	width={size}
	height={size}
	viewBox="0 0 24 24"
	fill={filled.has(name) ? 'currentColor' : 'none'}
	stroke={filled.has(name) ? 'none' : 'currentColor'}
	stroke-width="1.8"
	stroke-linecap="round"
	stroke-linejoin="round"
	aria-hidden={label ? undefined : 'true'}
	aria-label={label || undefined}
	role={label ? 'img' : undefined}
><path d={paths[name]} /></svg>
