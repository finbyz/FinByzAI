// Copyright (c) 2025, Finbyz Tech Pvt Ltd and contributors
// For license information, please see license.txt

frappe.ui.form.on("Gemini Cache", {
    refresh(frm) {
       frm.add_custom_button('Update Cache', () => {
			frappe.confirm(__('Are you sure you want to update cache?'), () => {
                frm.call({
                    doc: frm.doc,
                    freeze: true,
                    freeze_message: __("Please wait, cache getting update..."),
                    method: "update_cache",
                    args: {
                        dt: "Gemini Cache",
                        dn: frm.doc.name,
                    },
                });
			});
		})
    }
});
