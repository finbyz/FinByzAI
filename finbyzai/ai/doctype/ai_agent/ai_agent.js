// Copyright (c) 2025, sandeep and contributors
// For license information, please see license.txt

function extractVariables(msgs) {
  // Match {var} but not {{var}} or }}var}}
  const varRegex = /(?<!\{)\{([^{}]+)\}(?!\})/g;
  const perMessage = {};
  const seen = new Set();
  const unique = [];

  for (const msg of msgs) {
    const text = msg.content || "";
    const vars = [];
    let match;
    // Find all occurrences in order
    while ((match = varRegex.exec(text)) !== null) {
      const raw = match[1].trim();
      // Skip empty braces like {} if present
      if (!raw) continue;
      vars.push(raw);
      if (!seen.has(raw)) {
        seen.add(raw);
        unique.push(raw);
      }
    }
    perMessage[msg.name || "(unknown)"] = vars;
  }

  return { perMessage, unique };
}

frappe.ui.form.on("AI Agent", {
    refresh(frm) {
        if (frm.doc.name) {
            frm.add_custom_button(__("Test Agent"), function() {
                frm.trigger("show_test_dialog");
            });
        }
    },
    
    onload(frm){
        frm.trigger('agent_type')
        frm.trigger('llm_provider')
    },
    
    agent_type(frm) {
        if (frm.doc.agent_type == "Gemini Cache Agent") {
            frm.set_value("llm_provider", null);
            frm.set_value("llm", null);
        }
        let fields_to_hide_and_clear = ['output_schema', 'lc_agent_type', 'tools'];

        let hide_fields = ["Image Generation Agent"].includes(frm.doc.agent_type);

        fields_to_hide_and_clear.forEach(field => {
            frm.toggle_display(field, !hide_fields);
        });
    },
    
    llm_provider: function (frm) {
        frm.set_query('llm', function () {
            return {
                filters: {
                    provider: frm.doc.llm_provider,
                    supports_image_generation: frm.doc.agent_type === "Image Generation Agent"
                }
            };
        });
    },
    
    show_test_dialog(frm) {
        const variables = extractVariables(frm.doc.messages)
        const vars_fields = variables.unique
            .filter(v => v !== "format_instructions")
            .map(v => ({
                fieldtype: "Data",
                fieldname: v,
                label: __(frappe.utils.to_title_case(v.replaceAll("_", " "))),
                reqd: 0,
                placeholder: __("Enter " + v)
            }));

        let dialog = new frappe.ui.Dialog({
            title: __("Test AI Agent"),
            fields: [
                {
                    fieldtype: "Small Text",
                    fieldname: "input",
                    label: __("Query"),
                    reqd: 1,
                    placeholder: __("Enter your test query here...")
                },
                {
                    fieldtype: "Section Break",
                    fieldname: "variables_section",
                    label: __("Variables"),
                    hidden: vars_fields.length === 0
                },
                ...vars_fields
            ],
            primary_action_label: __("Test"),
            primary_action: function(values) {
                // Validate that query is not empty
                if (!values.input || !values.input.trim()) {
                    frappe.msgprint({
                        title: __('Validation Error'),
                        message: __('Please enter a query'),
                        indicator: 'red'
                    });
                    return;
                }

                console.log("Sending values to test_agent:", values);

                frappe.call({
                    method: "test_agent",
                    args: values,  // This sends all dialog values including 'input'
                    doc: frm.doc,
                    freeze: true,
                    freeze_message: __("Testing AI Agent... Please wait"),
                    callback: function(r) {
                        if (r.message) {
                            frappe.msgprint({
                                title: r.message.success ? __('Success') : __('Error'),
                                message: r.message.success
                                    ? `<pre style="white-space:pre-wrap;word-wrap:break-word;">${JSON.stringify(r.message.response, null, 2)}</pre>`
                                    : __(r.message.error || 'Unknown error'),
                                indicator: r.message.success ? 'green' : 'red'
                            });
                        }
                    },
                    error: function(err) {
                        console.error("Error calling test_agent:", err);
                        frappe.msgprint({
                            title: __('Error'),
                            message: __("Error testing agent: ") + (err.message || err),
                            indicator: 'red'
                        });
                    }
                });
                
                dialog.hide();
            }
        });
        
        dialog.show();
    }
});
