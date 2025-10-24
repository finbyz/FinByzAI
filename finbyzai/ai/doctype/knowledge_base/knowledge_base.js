// Copyright (c) 2025, Finbyz Tech Pvt Ltd and contributors
// For license information, please see license.txt

frappe.ui.form.on("Knowledge Base", {
	refresh(frm) {
		frm.add_custom_button("Update Knowledge", async () => {
			try {
				await frm.call("upsert");
				frappe.show_alert({ message: __("Knowledge updated"), indicator: "green" });
				frm.reload_doc();
			} catch (e) {
				frappe.msgprint({ title: __("Error"), message: e?.message || e, indicator: "red" });
			}
		});
	},
});
