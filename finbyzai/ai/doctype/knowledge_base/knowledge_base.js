// Copyright (c) 2025, Finbyz Tech Pvt Ltd and contributors
// For license information, please see license.txt

frappe.ui.form.on("Knowledge Base", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}
		
		frm.clear_custom_buttons();
		
		// Simple one-click upload
		frm.add_custom_button(__("Add Files"), () => {
			upload_and_add_files(frm);
		});
		
		// Primary action: Extract text and index all unprocessed documents
		frm.add_custom_button(__("Process & Index"), async () => {
			if (frm.is_dirty()) {
				frappe.msgprint({
					title: __("Save Changes"),
					message: __("Please save the document before processing."),
					indicator: "orange"
				});
				return;
			}
			
			const unprocessed = (frm.doc.documents || []).filter(row => !row.is_process);
			if (unprocessed.length === 0) {
				frappe.msgprint({
					title: __("Nothing to Process"),
					message: __("All documents have already been processed."),
					indicator: "blue"
				});
				return;
			}
			
			frappe.confirm(
				__('Process and index {0} unprocessed document(s)?', [unprocessed.length]),
				async () => {
					frappe.dom.freeze(__('Processing documents...'));
					
					try {
						await frappe.call({
							method: 'extract_all_files',
							doc: frm.doc
						});
						
						await frappe.call({
							method: 'upsert',
							doc: frm.doc
						});
						
						frappe.dom.unfreeze();
						frappe.show_alert({
							message: __('Processing completed successfully'),
							indicator: 'green'
						}, 5);
						frm.reload_doc();
						
					} catch (e) {
						frappe.dom.unfreeze();
						frappe.msgprint({
							title: __("Error"),
							message: e?.message || String(e),
							indicator: "red"
						});
					}
				}
			);
		}).addClass("btn-primary");
		
		// Extract text only
		frm.add_custom_button(__('Extract Text Only'), () => {
			if (frm.is_dirty()) {
				frappe.msgprint(__("Please save first"), __("Save Required"));
				return;
			}
			
			frappe.call({
				method: 'extract_all_files',
				doc: frm.doc,
				freeze: true,
				freeze_message: __('Extracting text...'),
				callback: (r) => frm.reload_doc()
			});
		}, __('Actions'));
		
		// Reprocess all
		frm.add_custom_button(__('Mark for Reprocessing'), () => {
			const processed = (frm.doc.documents || []).filter(row => row.is_process).length;
			
			if (processed === 0) {
				frappe.msgprint(__("No processed documents found"));
				return;
			}
			
			frappe.confirm(
				__("Mark {0} document(s) for re-indexing?", [processed]),
				() => {
					frappe.call({
						method: 'reprocess_all_documents',
						doc: frm.doc,
						callback: (r) => frm.reload_doc()
					});
				}
			);
		}, __('Actions'));
		
		// Clear all
		frm.add_custom_button(__('Clear All'), () => {
			frappe.confirm(
				__('Remove all documents?'),
				() => {
					frm.clear_table('documents');
					frm.refresh_field('documents');
					frappe.show_alert(__('Cleared. Click Save to confirm.'), 5);
				}
			);
		}, __('Actions'));
	},
});

/**
 * Upload files and add them to the knowledge base
 */
function upload_and_add_files(frm) {
	let uploaded_files = [];
	let upload_complete = false;
	
	const uploader = new frappe.ui.FileUploader({
		folder: 'Home/Attachments',
		allow_multiple: true,
		restrictions: {
			allowed_file_types: ['.pdf', '.xlsx', '.xls', '.csv', '.txt', '.md']
		},
		on_success: (file_doc) => {
			// Each successful upload
			uploaded_files.push({
				file_url: file_doc.file_url,
				file_name: file_doc.file_name
			});
			
			console.log('File uploaded:', file_doc.file_name);
		}
	});
	
	// Monitor when the uploader dialog is closed/hidden
	const check_interval = setInterval(() => {
		// Check if dialog is closed
		if (uploader.dialog && uploader.dialog.$wrapper && !uploader.dialog.$wrapper.is(':visible')) {
			clearInterval(check_interval);
			
			// Give a small delay to ensure all files are processed
			setTimeout(() => {
				if (uploaded_files.length > 0 && !upload_complete) {
					upload_complete = true;
					show_add_confirmation(frm, uploaded_files);
				}
			}, 500);
		}
	}, 500);
}

/**
 * Show confirmation dialog after files are uploaded
 */
function show_add_confirmation(frm, uploaded_files) {
	// Build file list HTML
	let file_list_html = '<div style="max-height: 300px; overflow-y: auto; padding: 10px; background: #f9f9f9; border-radius: 5px;">';
	file_list_html += '<table class="table table-bordered" style="margin: 0;">';
	file_list_html += '<thead><tr><th style="width: 40px;">#</th><th>File Name</th></tr></thead>';
	file_list_html += '<tbody>';
	
	uploaded_files.forEach((file, index) => {
		file_list_html += `<tr>
			<td>${index + 1}</td>
			<td><i class="fa fa-file text-success"></i> ${file.file_name}</td>
		</tr>`;
	});
	
	file_list_html += '</tbody></table></div>';
	
	let d = new frappe.ui.Dialog({
		title: __('Add {0} File(s) to Knowledge Base?', [uploaded_files.length]),
		fields: [
			{
				fieldname: 'info',
				fieldtype: 'HTML',
				options: '<p class="text-muted">' + __('The following files were uploaded successfully:') + '</p>'
			},
			{
				fieldname: 'files',
				fieldtype: 'HTML',
				options: file_list_html
			}
		],
		size: 'large',
		primary_action_label: __('Add to Knowledge Base'),
		primary_action: function() {
			add_files_to_table(frm, uploaded_files);
			d.hide();
		},
		secondary_action_label: __('Cancel'),
		secondary_action: function() {
			frappe.show_alert({
				message: __('Files uploaded but not added to Knowledge Base'),
				indicator: 'orange'
			}, 5);
		}
	});
	
	d.show();
}

/**
 * Add uploaded files to child table
 */
function add_files_to_table(frm, uploaded_files) {
	let added = 0;
	let duplicates = 0;
	let existing_files = new Set((frm.doc.documents || []).map(r => r.file));
	
	uploaded_files.forEach(file => {
		if (existing_files.has(file.file_url)) {
			duplicates++;
			return;
		}
		
		let row = frm.add_child('documents');
		row.file = file.file_url;
		row.is_process = 0;
		added++;
		existing_files.add(file.file_url);
	});
	
	if (added > 0) {
		frm.refresh_field('documents');
		
		let msg = __('Added {0} file(s) to the table.', [added]);
		if (duplicates > 0) {
			msg += ' ' + __('Skipped {0} duplicate(s).', [duplicates]);
		}
		msg += ' <strong>' + __('Please click Save to confirm.') + '</strong>';
		
		frappe.show_alert({
			message: msg,
			indicator: 'green'
		}, 8);
	} else if (duplicates > 0) {
		frappe.msgprint({
			title: __('Duplicates Found'),
			message: __('All {0} file(s) already exist in the table.', [duplicates]),
			indicator: 'orange'
		});
	}
}

// Child table events
frappe.ui.form.on("Knowledge Base Document", {
	file: function(frm, cdt, cdn) {
		let row = locals[cdt][cdn];
		if (row.file) {
			frappe.model.set_value(cdt, cdn, 'is_process', 0);
		}
	}
});