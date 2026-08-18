frappe.listview_settings['Automation Dead Letter'] = {
	onload: function(listview) {
		listview.page.add_action_item(__("Retry Selected"), function() {
			let docnames = listview.get_checked_items(true);
			if (docnames.length > 0) {
				frappe.confirm(__("Are you sure you want to retry {0} dead letters?", [docnames.length]), () => {
					frappe.call({
						method: 'finbyzai.workflow_builder.observability.bulk_retry_dead_letters',
						args: {
							dead_letter_names: docnames
						},
						freeze: true,
						callback: function(r) {
							if (!r.exc) {
								frappe.msgprint(__("Successfully queued {0} items for retry.", [r.message.count]));
								listview.refresh();
							}
						}
					});
				});
			}
		});

		listview.page.add_action_item(__("Discard Selected"), function() {
			let docnames = listview.get_checked_items(true);
			if (docnames.length > 0) {
				frappe.confirm(__("Are you sure you want to discard {0} dead letters?", [docnames.length]), () => {
					frappe.call({
						method: 'finbyzai.workflow_builder.observability.bulk_discard_dead_letters',
						args: {
							dead_letter_names: docnames
						},
						freeze: true,
						callback: function(r) {
							if (!r.exc) {
								frappe.msgprint(__("Successfully discarded {0} items.", [r.message.count]));
								listview.refresh();
							}
						}
					});
				});
			}
		});
	}
};
