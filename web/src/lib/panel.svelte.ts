/** Panel state — which screenshot is currently open in the detail panel. */

let _id = $state<number | null>(null);
let _siblings = $state<number[]>([]);

export const panel = {
	get id() { return _id; },
	get siblings() { return _siblings; },
	open(id: number, siblings: number[] = []) {
		_id = id;
		_siblings = siblings;
	},
	close() { _id = null; _siblings = []; },
	prev() {
		if (_id === null || _siblings.length === 0) return;
		const i = _siblings.indexOf(_id);
		if (i > 0) _id = _siblings[i - 1];
	},
	next() {
		if (_id === null || _siblings.length === 0) return;
		const i = _siblings.indexOf(_id);
		if (i < _siblings.length - 1) _id = _siblings[i + 1];
	},
};
