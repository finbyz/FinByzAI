<script setup>
import AttachmentChip from "./AttachmentChip.vue";

// The user's own turn. Right-aligned and on a gray ground because the eye needs
// to find where its question ended and the answer began when scrolling back
// through a long conversation — that is the whole job, so it gets one radius
// from frappe-ui's scale and nothing else.
defineProps({
	content: { type: String, default: "" },
	attachments: { type: Array, default: () => [] },
});
</script>

<template>
	<div class="flex flex-col items-end gap-1.5">
		<div v-if="attachments.length" class="flex max-w-[85%] flex-wrap justify-end gap-1.5">
			<AttachmentChip
				v-for="(a, i) in attachments"
				:key="`${a.file_name}-${i}`"
				:file-name="a.file_name"
				:file-size="a.file_size"
			/>
		</div>
		<div
			v-if="content"
			class="max-w-[85%] whitespace-pre-wrap break-words rounded-lg bg-surface-gray-2 px-3 py-2 text-p-base text-ink-gray-8"
		>
			{{ content }}
		</div>
	</div>
</template>
