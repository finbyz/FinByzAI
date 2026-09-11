<script setup>
// An anchored menu that renders in place.
//
// frappe-ui ships `Dropdown`, and we cannot use it: it renders through a portal to
// `document.body`, and this bundle's CSS is scoped to `#copilot-root`, so the menu
// arrives with none of its styling. Same trap that made the settings dialog invisible.
// Everything the panel overlays has to live in the panel.
//
// So this is ours, but it is not a new design: the surface, row shell and states are
// frappe-ui's (`bg-surface-white` + `shadow-2xl` for a menu, `ItemListRow`'s
// prefix/label/suffix regions, `bg-surface-gray-2` for the active row).
import { computed, nextTick, ref, watch } from "vue";
import { __ } from "@/lib/translate";

const props = defineProps({
	// [{ value, label, logo?, icon?, hint?, group?, danger? }]
	items: { type: Array, default: () => [] },
	modelValue: { default: null },
	align: { type: String, default: "left" }, // left | right
	side: { type: String, default: "top" }, // top | bottom — where the menu opens
	searchable: { type: Boolean, default: false },
	searchThreshold: { type: Number, default: 7 },
	disabled: { type: Boolean, default: false },
	width: { type: String, default: "min-w-[13rem] max-w-[17rem]" },
});
const emit = defineEmits(["update:modelValue", "select"]);

const open = ref(false);
const query = ref("");
const cursor = ref(-1);
const search = ref(null);

const showSearch = computed(
	() => props.searchable && props.items.length >= props.searchThreshold
);

const filtered = computed(() => {
	const needle = query.value.trim().toLowerCase();
	if (!needle) return props.items;
	return props.items.filter((item) =>
		`${item.label} ${item.group || ""} ${item.hint || ""}`.toLowerCase().includes(needle)
	);
});

function toggle() {
	if (props.disabled) return;
	open.value = !open.value;
	if (open.value) {
		query.value = "";
		cursor.value = filtered.value.findIndex((i) => i.value === props.modelValue);
		if (showSearch.value) nextTick(() => search.value?.focus());
	}
}

function close() {
	open.value = false;
}

function choose(item) {
	if (item.disabled) return;
	emit("update:modelValue", item.value);
	emit("select", item);
	close();
}

// Arrow keys move, Enter picks, Escape closes — a menu you cannot drive from the
// keyboard is a menu half the users cannot drive at all.
function onKeydown(event) {
	if (!open.value) return;
	const list = filtered.value;
	if (event.key === "Escape") {
		event.stopPropagation();
		close();
	} else if (event.key === "ArrowDown") {
		event.preventDefault();
		cursor.value = (cursor.value + 1) % Math.max(1, list.length);
	} else if (event.key === "ArrowUp") {
		event.preventDefault();
		cursor.value = (cursor.value - 1 + list.length) % Math.max(1, list.length);
	} else if (event.key === "Enter" && list[cursor.value]) {
		event.preventDefault();
		choose(list[cursor.value]);
	}
}

watch(open, (isOpen) => {
	if (isOpen) document.addEventListener("keydown", onKeydown, true);
	else document.removeEventListener("keydown", onKeydown, true);
});
</script>

<template>
	<div class="relative">
		<slot name="trigger" :toggle="toggle" :open="open" />

		<template v-if="open">
			<!-- A click anywhere else closes, without stealing the click that opened it. -->
			<div class="fixed inset-0 z-40" @click="close"></div>
			<div
				class="absolute z-50 overflow-hidden rounded-lg border border-outline-gray-2 bg-surface-white shadow-2xl"
				:class="[
					width,
					align === 'right' ? 'right-0' : 'left-0',
					side === 'top' ? 'bottom-[calc(100%+6px)]' : 'top-[calc(100%+6px)]',
				]"
			>
				<div v-if="showSearch" class="border-b border-outline-gray-1 p-1.5">
					<input
						ref="search"
						v-model="query"
						type="text"
						:placeholder="__('Search…')"
						class="h-7 w-full rounded bg-surface-gray-2 px-2 text-base text-ink-gray-8 outline-none placeholder:text-ink-gray-4"
					/>
				</div>

				<div class="max-h-72 overflow-y-auto p-1">
					<template v-for="(item, index) in filtered" :key="item.value ?? `i${index}`">
						<div
							v-if="item.group && item.group !== filtered[index - 1]?.group"
							class="px-2 pb-0.5 pt-2 text-2xs text-ink-gray-5"
						>
							{{ item.group }}
						</div>
						<button
							class="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left"
							:class="[
								index === cursor ? 'bg-surface-gray-2' : 'hover:bg-surface-gray-2',
								item.danger ? 'text-ink-red-4' : 'text-ink-gray-8',
							]"
							@click="choose(item)"
							@mousemove="cursor = index"
						>
							<img v-if="item.logo" :src="item.logo" class="copilot-logo size-3.5 shrink-0" alt="" />
							<span
								v-else-if="item.icon"
								class="size-3.5 shrink-0 text-ink-gray-6"
								:class="item.icon"
								aria-hidden="true"
							></span>
							<span class="min-w-0 flex-1">
								<span class="block truncate text-base">{{ item.label }}</span>
								<span v-if="item.hint" class="block truncate text-2xs text-ink-gray-5">{{
									item.hint
								}}</span>
							</span>
							<span
								v-if="item.value === modelValue"
								class="lucide-check size-3.5 shrink-0 text-ink-gray-7"
								aria-hidden="true"
							></span>
						</button>
					</template>

					<div v-if="!filtered.length" class="px-2 py-4 text-center text-base text-ink-gray-5">
						{{ __("No matches") }}
					</div>
				</div>
			</div>
		</template>
	</div>
</template>
