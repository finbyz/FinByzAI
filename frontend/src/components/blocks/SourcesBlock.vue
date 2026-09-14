<script setup>
import { hostnameOf } from "@/lib/tools";
import { __ } from "@/lib/translate";

// The pages an external search actually read, as a row of clickable cards — the
// bibliography a real answer earns, in the place a reader looks for one: after
// the prose that used them, never before it. `AssistantMessage.vue` pulls every
// "sources" block out of the turn's normal top-to-bottom order and renders one
// deduplicated row here, once, at the very end of the message — Claude's own
// citation strip is the reference point, not this app's usual "block sits where
// the tool call that produced it happened" rule, which would put a bibliography
// above the paragraph it supports.
//
// A card, not a numbered footnote list: there is no in-text "[1]" to anchor a
// list item to (the model writes prose, not citation markers), so a footnote
// number would point at nothing. A card carries its own context — title, domain
// — and needs no anchor.
defineProps({ items: { type: Array, required: true } });
</script>

<template>
	<div class="flex flex-col gap-1.5">
		<div class="flex items-center gap-1.5 text-xs text-ink-gray-4">
			<span class="lucide-globe size-3" aria-hidden="true"></span>
			{{ __("Sources") }}
		</div>
		<div class="copilot-scrollbar -mx-px flex gap-2 overflow-x-auto px-px pb-1">
			<a
				v-for="(item, i) in items"
				:key="item.url ?? i"
				:href="item.url"
				target="_blank"
				rel="noopener noreferrer"
				:title="item.snippet || item.title"
				class="flex w-44 shrink-0 flex-col gap-1 rounded-lg border border-outline-gray-2 bg-surface-white p-2.5 transition-colors hover:border-outline-gray-3 hover:bg-surface-gray-1"
			>
				<p class="line-clamp-2 text-p-sm text-ink-gray-8">{{ item.title }}</p>
				<p class="flex min-w-0 items-center gap-1 text-2xs text-ink-gray-4">
					<span class="lucide-arrow-up-right size-3 shrink-0" aria-hidden="true"></span>
					<span class="truncate">{{ hostnameOf(item.url) }}</span>
				</p>
			</a>
		</div>
	</div>
</template>
