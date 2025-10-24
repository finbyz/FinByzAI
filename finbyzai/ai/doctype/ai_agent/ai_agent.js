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
            frm.doc.llm_provider = null
            frm.doc.llm = null
            frm.refresh_fields()
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
                    label: __("Variables")
                },
                ...vars_fields
            ],
            primary_action_label: __("Test"),
            primary_action: function(values) {
                frappe.call({
                    method: "test_agent",
                    args: values,
                    doc: frm.doc,
                    freeze: true,
                    freeze_message: __("Testing AI Agent... Please wait"),
                    callback: function(r) {
                        if (r.message) {
                            frappe.msgprint(frappe.markdown(r?.message?.response?.output || r?.message?.response || r?.message || r))
                        }
                    },
                    error: function(err) {
                        frappe.msgprint(__("Error testing agent: ") + err.message);
                    }
                })
                // dialog.hide();
            }
        });
        
        dialog.show();
    },
    
    format_test_result(frm, result) {
        let html = `
            <div class="test-result-container" style="padding: 15px;">
                <div class="test-status" style="margin-bottom: 15px;">
                    <h4 style="color: ${result.success ? 'green' : 'red'};">
                        ${result.success ? '✓ Test Successful' : '✗ Test Failed'}
                    </h4>
                </div>
                
                <div class="test-details" style="margin-bottom: 15px;">
                    <strong>Query:</strong> ${result.query}<br>
                    <strong>Agent Type:</strong> ${result.agent_type}<br>
                    <strong>LLM:</strong> ${result.llm}<br>
                    ${result.variables && Object.keys(result.variables).length > 0 ? 
                        `<strong>Variables:</strong> <pre style="background: #f5f5f5; padding: 5px; border-radius: 3px;">${JSON.stringify(result.variables, null, 2)}</pre>` : 
                        ''
                    }
                </div>
                
                <div class="test-response" style="margin-bottom: 15px;">
                    <strong>Response:</strong>
                    <div style="background: #f9f9f9; padding: 10px; border-radius: 5px; margin-top: 5px; max-height: 300px; overflow-y: auto;">
                        ${result.success ? 
                            (typeof result.response === 'object' ? 
                                `<pre>${JSON.stringify(result.response, null, 2)}</pre>` : 
                                result.response
                            ) : 
                            `<span style="color: red;">${result.error}</span>`
                        }
                    </div>
                </div>
            </div>
        `;
        return html;
    }
});
