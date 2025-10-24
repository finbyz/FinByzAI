// Copyright (c) 2025, Finbyz Tech Pvt Ltd and contributors
// For license information, please see license.txt

frappe.ui.form.on("AI Tool", {
	refresh(frm) {
        if (frappe.boot.developer_mode == 0){
            frm.get_field('module').df.hidden = 1
            frm.get_field('is_custom').df.hidden = 1
            frm.refresh_fields()
        }
    }
});
