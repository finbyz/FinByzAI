// Copyright (c) 2025, Finbyz Tech Pvt Ltd and contributors
// For license information, please see license.txt

frappe.ui.form.on("AI Tool", {
	refresh(frm) {
        if (frappe.boot.developer_mode == 0){
            frm.get_field('module').df.hidden = 1
            frm.get_field('is_custom').df.hidden = 1
            frm.refresh_fields()
        }
        frm.trigger("tool_type");
    },

    tool_type(frm) {
        const is_builtin = frm.doc.tool_type === "Provider Built-in";
        frm.set_df_property(
            "execution_side",
            "description",
            is_builtin
                ? __("Executed and billed by the model provider.")
                : __("Executed by this Frappe application.")
        );
        if (frm.doc.execution_side !== (is_builtin ? "Provider" : "Application")) {
            frm.set_value("execution_side", is_builtin ? "Provider" : "Application");
        }
    },
});
