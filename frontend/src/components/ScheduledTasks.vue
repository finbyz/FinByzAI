<script setup>
import { onMounted, onUnmounted, ref } from "vue";
import { Button } from "@/lib/ui";
import * as api from "@/api/schedules";
import { __ } from "@/lib/translate";

const props = defineProps({ store: { type: Object, required: true } });
const emit = defineEmits(["close"]);
const tasks = ref([]);
const draft = ref(null);
const busy = ref(false);
const loading = ref(true);
const error = ref("");
const deleting = ref(null);
const titleInput = ref(null);
const repeatOptions = ["Once", "Hourly", "Daily", "Weekly", "Monthly", "Custom"];
const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
const originalFocus = document.activeElement;
let refreshTimer;

async function refresh() {
    try { tasks.value = await api.listTasks(); }
    catch (e) { error.value = e.message || __("Could not load tasks"); }
    finally { loading.value = false; }
}

function edit(task = null) {
    error.value = "";
    deleting.value = null;
    draft.value = task ? { ...task } : {
        title: "", prompt: "", scheduled_for: "", timezone,
        recurrence: "Once", cron_expression: "", enabled: 1,
        agent: props.store.selectedAgent.value,
        model: props.store.selectedModel.value,
        knowledge_base: props.store.selectedKnowledgeBase.value,
    };
    requestAnimationFrame(() => titleInput.value?.focus());
}

async function action(fn) {
    busy.value = true;
    error.value = "";
    try { await fn(); await refresh(); }
    catch (e) { error.value = e.message || __("Could not update task"); }
    finally { busy.value = false; }
}

function save() {
    action(async () => {
        await api.saveTask(draft.value, draft.value.name);
        draft.value = null;
    });
}

async function viewResult(task) {
    await action(async () => {
        if (props.store.sending.value) throw new Error(__("Wait for the current chat to finish before opening a result."));
        await props.store.switchSession(task.last_conversation);
        emit("close");
    });
}

function displayTime(value, zone) {
    if (!value) return "";
    return new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium", timeStyle: "short", timeZone: zone,
    }).format(new Date(value));
}

function status(task) {
    if (task.error) return __("Needs attention");
    if (!task.enabled) return __("Paused");
    if (task.run_status === "Paused") return __("Awaiting approval");
    if (task.run_status === "Running") return __("Running");
    if (task.completed) return __("Finished");
    return __("Scheduled");
}

function onKey(event) {
    if (event.key === "Escape" && !busy.value) {
        event.stopPropagation();
        if (draft.value) draft.value = null;
        else emit("close");
    }
    if (event.key !== "Tab") return;
    const nodes = [...document.querySelectorAll('.copilot-tasks button, .copilot-tasks input, .copilot-tasks textarea, .copilot-tasks select')].filter(n => !n.disabled);
    const first = nodes[0], last = nodes.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
}

onMounted(() => {
    refresh();
    refreshTimer = setInterval(refresh, 15000);
    document.addEventListener("keydown", onKey, true);
    document.querySelector('.copilot-tasks button')?.focus();
});
onUnmounted(() => {
    clearInterval(refreshTimer);
    document.removeEventListener("keydown", onKey, true);
    originalFocus?.focus();
});
</script>

<template>
    <div class="absolute inset-0 z-50 flex items-center justify-center p-2" :class="store.fullscreen.value ? 'bg-black-overlay-200' : ''">
        <section role="dialog" aria-modal="true" aria-labelledby="copilot-tasks-title" class="copilot-tasks flex h-full w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-outline-gray-2 bg-surface-white shadow-lg" :aria-busy="busy">
            <header class="flex items-center gap-2 border-b border-outline-gray-1 p-4">
                <span class="lucide-calendar-clock size-4" aria-hidden="true" />
                <h2 id="copilot-tasks-title" class="flex-1 text-base font-semibold">{{ __("Scheduled tasks") }}</h2>
                <Button v-if="!draft" :disabled="busy" @click="edit()">{{ __("New task") }}</Button>
                <Button variant="ghost" icon="lucide-x" :aria-label="__('Close scheduled tasks')" :disabled="busy" @click="emit('close')" />
            </header>
            <div class="min-h-0 flex-1 overflow-y-auto p-4">
                <p v-if="error" role="alert" class="mb-3 text-sm text-ink-red-4">{{ error }}</p>
                <form v-if="draft" class="flex flex-col gap-4" @submit.prevent="save">
                    <label class="flex flex-col gap-1 text-sm">{{ __("Title") }}<input ref="titleInput" v-model="draft.title" required maxlength="140" class="task-input" /></label>
                    <label class="flex flex-col gap-1 text-sm">{{ __("Task instructions") }}<textarea v-model="draft.prompt" required maxlength="20000" rows="4" class="task-input" :placeholder="__('Summarize overdue invoices and highlight the top five customers.')" /></label>
                    <p class="text-xs text-ink-gray-5">{{ __("Include everything Copilot needs. Each occurrence starts a new chat.") }}</p>
                    <label class="flex flex-col gap-1 text-sm">{{ __("Start date and time") }}<input v-model="draft.scheduled_for" type="datetime-local" required class="task-input" /></label>
                    <label class="flex flex-col gap-1 text-sm">{{ __("Timezone") }}<input v-model="draft.timezone" required class="task-input" placeholder="Asia/Kolkata" /></label>
                    <label class="flex flex-col gap-1 text-sm">{{ __("Repeat") }}<select v-model="draft.recurrence" class="task-input"><option v-for="item in repeatOptions" :key="item" :value="item">{{ __(item) }}</option></select></label>
                    <label v-if="draft.recurrence === 'Custom'" class="flex flex-col gap-1 text-sm">{{ __("Cron expression") }}<input v-model="draft.cron_expression" required class="task-input" placeholder="0 9 * * 1-5" /><span class="text-xs text-ink-gray-5">{{ __("Minute, hour, day, month, weekday. Example: 0 9 * * 1-5 runs weekdays at 9 AM after the start date.") }}</span></label>
                    <label class="flex flex-col gap-1 text-sm">{{ __("Agent") }}<select v-model="draft.agent" class="task-input"><option :value="null">{{ __("Site default") }}</option><option v-for="agent in store.agents.value" :key="agent.name" :value="agent.name">{{ agent.title }}</option></select></label>
                    <label class="flex flex-col gap-1 text-sm">{{ __("Model") }}<select v-model="draft.model" class="task-input"><option :value="null">{{ __("Agent default") }}</option><option v-for="model in store.models.value" :key="model.name" :value="model.name">{{ model.title }}</option></select></label>
                    <label class="flex flex-col gap-1 text-sm">{{ __("Knowledge base") }}<select v-model="draft.knowledge_base" class="task-input"><option :value="null">{{ __("Agent default") }}</option><option v-for="kb in store.knowledgeBases.value" :key="kb.name" :value="kb.name">{{ kb.title }}</option></select></label>
                    <div class="flex gap-2"><Button type="submit" variant="solid" :loading="busy">{{ __("Save task") }}</Button><Button :disabled="busy" @click="draft = null">{{ __("Cancel") }}</Button></div>
                </form>
                <template v-else>
                    <p class="mb-4 text-sm text-ink-gray-5">{{ __("Tasks run when you are away. Results appear in chat and notifications. Your existing approval settings apply.") }}</p>
                    <p v-if="loading" role="status" class="text-sm text-ink-gray-5">{{ __("Loading tasks…") }}</p>
                    <div v-else-if="!tasks.length" class="rounded-lg border border-outline-gray-1 p-6 text-center"><p class="mb-2 font-medium">{{ __("No scheduled tasks yet") }}</p><p class="text-sm text-ink-gray-5">{{ __("Create a task here, or ask Copilot to schedule one. Chat scheduling requires the schedule_task tool enabled on your agent.") }}</p></div>
                    <article v-for="task in tasks" :key="task.name" class="mb-3 rounded-lg border border-outline-gray-2 p-3">
                        <div class="flex items-start justify-between gap-2"><h3 class="break-words text-sm font-semibold">{{ task.title }}</h3><span class="shrink-0 text-xs text-ink-gray-5">{{ status(task) }}</span></div>
                        <p class="mt-2 whitespace-pre-wrap break-words text-sm text-ink-gray-6">{{ task.prompt }}</p>
                        <p class="mt-2 text-xs text-ink-gray-5">{{ __(task.recurrence) }} · {{ task.timezone }}</p>
                        <p v-if="task.next_run && task.enabled" class="mt-1 text-xs text-ink-gray-5">{{ __("Next run") }}: {{ displayTime(task.next_run, task.timezone) }}</p>
                        <p v-if="task.error" class="mt-2 text-sm text-ink-red-4">{{ task.error }}</p>
                        <p v-if="task.run_status === 'Failed'" class="mt-2 text-sm text-ink-red-4">{{ __("Latest run failed. Open the result for details.") }}</p>
                        <div class="mt-3 flex flex-wrap gap-2">
                            <Button :disabled="busy" @click="edit(task)">{{ __("Edit") }}</Button>
                            <Button v-if="!task.completed" :disabled="busy" @click="action(() => api.setEnabled(task.name, !task.enabled))">{{ task.enabled ? __("Pause") : __("Resume") }}</Button>
                            <Button v-if="task.last_conversation" :disabled="busy" @click="viewResult(task)">{{ __("View result") }}</Button>
                            <Button variant="ghost" :disabled="busy" @click="deleting = task.name">{{ __("Delete") }}</Button>
                        </div>
                        <div v-if="deleting === task.name" class="mt-3 flex flex-wrap items-center gap-2 text-sm"><span>{{ __("Delete this schedule?") }}</span><Button :disabled="busy" @click="action(() => api.deleteTask(task.name))">{{ __("Delete task") }}</Button><Button :disabled="busy" @click="deleting = null">{{ __("Cancel") }}</Button></div>
                    </article>
                    <p class="mt-4 text-xs text-ink-gray-5">{{ __("Start times depend on scheduler and worker availability. Missed recurring occurrences are skipped.") }}</p>
                </template>
            </div>
        </section>
    </div>
</template>

<style scoped>
.task-input { width: 100%; border: 1px solid var(--outline-gray-2, #d1d5db); border-radius: .5rem; padding: .5rem .75rem; color: inherit; background: var(--surface-white, white); }
.task-input:focus { outline: 2px solid var(--outline-gray-4, #9ca3af); outline-offset: 1px; }
</style>
