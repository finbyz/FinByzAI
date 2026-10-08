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

function applySchemaDefaults(schema, current = {}) {
  const result = { ...current };
  const properties = schema?.properties || {};

  Object.entries(properties).forEach(([key, definition]) => {
    if (result[key] === undefined && definition.default !== undefined) {
      result[key] = definition.default;
    }
    if (
      result[key] &&
      typeof result[key] === "object" &&
      !Array.isArray(result[key])
    ) {
      result[key] = applySchemaDefaults(definition, result[key]);
    }
  });

  return result;
}

frappe.ui.form.on("AI Agent", {
    refresh(frm) {
        frm.trigger("generation_controls");
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
        frm.trigger("generation_controls");
        let fields_to_hide_and_clear = ['output_schema', 'lc_agent_type', 'tools'];

        let hide_fields = ["Image Generation Agent"].includes(frm.doc.agent_type);

        fields_to_hide_and_clear.forEach(field => {
            frm.toggle_display(field, !hide_fields);
        });
    },
    
    llm(frm) {
        frm.trigger("generation_controls");
    },

    gemini_cache(frm) {
        frm.trigger("generation_controls");
    },

    async generation_controls(frm) {
        const request = (frm.generation_request || 0) + 1;
        frm.generation_request = request;
        let model = frm.doc.llm;
        if (frm.doc.agent_type === "Gemini Cache Agent" && frm.doc.gemini_cache) {
            const result = await frappe.db.get_value("Gemini Cache", frm.doc.gemini_cache, "llm");
            model = result.message?.llm;
        }
        if (!model || frm.doc.agent_type === "Image Generation Agent") {
            frm.set_df_property("thinking_level", "options", "Default\nLow\nMedium\nHigh");
            frm.set_df_property("temperature", "read_only", false);
            return;
        }
        const result = await frappe.call({
            method: "finbyzai.ai.generation.generation_capabilities",
            args: { model },
        });
        if (frm.generation_request !== request) return;
        const controls = result.message;
        const levels = [...controls.thinking_levels];
        const selected = frm.doc.thinking_level || "Default";
        if (!levels.includes(selected)) levels.push(selected);
        frm.set_df_property("thinking_level", "options", levels.join("\n"));
        frm.set_df_property("thinking_level", "description", controls.thinking_levels.includes(selected)
            ? __("Default uses the model default. Available levels depend on the selected model.")
            : __("The selected thinking level is unsupported. Choose a supported level before saving."));
        frm.set_df_property("temperature", "read_only", controls.ignores_sampling);
        frm.set_df_property("temperature", "description", controls.ignores_sampling
            ? __("This model uses its default sampling settings; temperature is ignored.")
            : __("Controls creativity where supported. Thinking models may ignore temperature."));
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

                const requestValues = {
                    ...values,
                    conversation_id: frm.__ai_test_conversation_id || null,
                };
                console.log("Sending values to test_agent:", requestValues);

                frappe.call({
                    method: "test_agent",
                    args: requestValues,
                    doc: frm.doc,
                    freeze: true,
                    freeze_message: __("Testing AI Agent... Please wait"),
                    callback: function(r) {
                        if (r.message) {
                            if (r.message.conversation_id) {
                                frm.__ai_test_conversation_id = r.message.conversation_id;
                            }
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

frappe.ui.form.on("AI Agent Tool", {
    async tool(frm, cdt, cdn) {
        const row = locals[cdt][cdn];
        if (!row.tool) {
            return;
        }

        const response = await frappe.db.get_value(
            "AI Tool",
            row.tool,
            ["tool_type", "configuration_schema"]
        );
        const values = response?.message || {};
        await frappe.model.set_value(
            cdt,
            cdn,
            "tool_type",
            values.tool_type || "Function"
        );

        if (values.tool_type !== "Provider Built-in" || row.configuration) {
            return;
        }

        try {
            const schema = JSON.parse(values.configuration_schema || "{}");
            const defaults = applySchemaDefaults(schema);
            await frappe.model.set_value(
                cdt,
                cdn,
                "configuration",
                JSON.stringify(defaults, null, 2)
            );
        } catch (error) {
            frappe.msgprint({
                title: __("Invalid Tool Configuration Schema"),
                message: error.message,
                indicator: "red",
            });
        }
    },
});
