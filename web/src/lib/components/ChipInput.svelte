<script>
	// A list of short values (people, tags) typed as chips. Enter or comma adds.
	let { values = $bindable([]), label, placeholder = '', suggestions = [], prefix = '' } = $props();
	let text = $state('');
	const id = 'chips-' + Math.random().toString(36).slice(2, 8);

	function add(raw) {
		const v = raw.trim().replace(/^#/, '');
		if (v && !values.includes(v)) values = [...values, v];
		text = '';
	}

	function key(e) {
		if (e.key === 'Enter' || e.key === ',') {
			e.preventDefault();
			add(text);
		} else if (e.key === 'Backspace' && !text && values.length) {
			values = values.slice(0, -1);
		}
	}

	function input() {
		if (text.includes(',')) text.split(',').forEach((p, i, all) => (i < all.length - 1 ? add(p) : (text = p)));
	}
</script>

<div class="field">
	<label for={id}><span>{label}</span></label>
	<div class="box">
		{#each values as v}
			<span class="chip">{prefix}{v}<button type="button" onclick={() => (values = values.filter((x) => x !== v))} aria-label="Remove {v}">×</button></span>
		{/each}
		<input {id} bind:value={text} onkeydown={key} oninput={input} onblur={() => add(text)}
			placeholder={values.length ? '' : placeholder} list={suggestions.length ? id + '-list' : undefined} autocomplete="off" />
	</div>
	{#if suggestions.length}
		<datalist id="{id}-list">{#each suggestions.filter((s) => !values.includes(s)) as s}<option value={s}></option>{/each}</datalist>
	{/if}
</div>

<style>
	.box {
		display: flex; flex-wrap: wrap; gap: 6px; align-items: center; padding: 6px 8px; border: 1px solid var(--line);
		border-radius: 10px; background: var(--bg);
	}
	.box:focus-within { outline: 2px solid var(--accent); outline-offset: -1px; border-color: transparent; }
	.box input { border: 0 !important; outline: none !important; padding: 4px !important; flex: 1; min-width: 120px; background: transparent !important; }
	.chip button { background: none; border: 0; color: var(--muted); cursor: pointer; padding: 0 0 0 2px; font-size: 1rem; line-height: 1; }
</style>
