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
                        console.log("Response from test_agent:", r);
                        return
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
    },
    
    format_test_result(frm, result) {
        let response_display = '';
        
        // Format response based on type
        if (typeof result.response === 'object') {
            // Check if it's a structured output with specific keys
            if (result.response.output) {
                response_display = frm.trigger("format_response_content", result.response.output);
            } else {
                response_display = `<pre style="background: #f9f9f9; padding: 10px; border-radius: 5px; white-space: pre-wrap; word-wrap: break-word;">${JSON.stringify(result.response, null, 2)}</pre>`;
            }
        } else if (typeof result.response === 'string') {
            // Try to parse as markdown or just display as text
            response_display = frappe.markdown(result.response) || result.response;
        } else {
            response_display = String(result.response);
        }

        let html = `
            <div class="test-result-container" style="padding: 15px;">
                <div class="test-status" style="margin-bottom: 15px;">
                    <h4 style="color: ${result.success ? 'green' : 'red'};">
                        ${result.success ? '✓ Test Successful' : '✗ Test Failed'}
                    </h4>
                </div>
                
                <div class="test-details" style="margin-bottom: 15px; background: #f5f5f5; padding: 10px; border-radius: 5px;">
                    <strong>Query:</strong> <span style="color: #333;">${result.query}</span><br>
                    <strong>Agent Type:</strong> ${result.agent_type}<br>
                    <strong>LLM:</strong> ${result.llm}<br>
                    ${result.memory_enabled ? '<strong>Memory:</strong> <span style="color: green;">Enabled</span><br>' : ''}
                    ${result.variables && Object.keys(result.variables).length > 0 ? 
                        `<strong>Variables:</strong> <pre style="background: #fff; padding: 5px; border-radius: 3px; margin-top: 5px;">${JSON.stringify(result.variables, null, 2)}</pre>` : 
                        ''
                    }
                </div>
                
                <div class="test-response" style="margin-bottom: 15px;">
                    <strong>Response:</strong>
                    <div style="background: #fff; padding: 15px; border-radius: 5px; margin-top: 5px; max-height: 400px; overflow-y: auto; border: 1px solid #e0e0e0;">
                        ${response_display}
                    </div>
                </div>
            </div>
        `;
        return html;
    },
    
    format_response_content(frm, content) {
        // Helper function to format different types of content
        if (typeof content === 'object') {
            return `<pre style="background: #f9f9f9; padding: 10px; border-radius: 5px; white-space: pre-wrap; word-wrap: break-word;">${JSON.stringify(content, null, 2)}</pre>`;
        } else if (typeof content === 'string') {
            // Check if it looks like markdown
            if (content.includes('#') || content.includes('*') || content.includes('[')) {
                return frappe.markdown(content);
            }
            return content;
        }
        return String(content);
    }
});
