const call = (method, args = {}) => frappe.xcall(`finbyzai.copilot.schedules.${method}`, args);

export const listTasks = () => call("list_tasks");
export const saveTask = (task, name) => call("save_task", { task, name });
export const setEnabled = (name, enabled) => call("set_enabled", { name, enabled: enabled ? 1 : 0 });
export const deleteTask = (name) => call("delete_task", { name });
