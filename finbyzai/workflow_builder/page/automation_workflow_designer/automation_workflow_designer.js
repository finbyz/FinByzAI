// Reuse the existing workflow React application inside the Desk workspace.
frappe.pages["automation-workflow-designer"].on_page_load = function (wrapper) {
    const page = frappe.ui.make_app_page({parent: wrapper, title: __("Workflow designer"), single_column: true});
    page.main.addClass("automation-workflow-designer");
    const frame = document.createElement("iframe");
    frame.className = "automation-workflow-designer-frame";
    frame.title = __("Automation workflow editor");
    page.main.append(frame);
    frame.addEventListener("load", () => {
        // Desk links must leave the embedded app, rather than nesting another Desk inside it.
        frame.contentDocument?.addEventListener("click", event => {
            const link = event.target.closest?.("a[href]");
            if (!link || link.target === "_blank" || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
            const url = new URL(link.href, window.location.origin);
            if (url.origin !== window.location.origin || !/^\/(app|desk)(\/|$)/.test(url.pathname)) return;
            event.preventDefault();
            if (/^\/(app|desk)\/?$/.test(url.pathname)) frappe.set_route("List", "Automation Workflow");
            else frappe.set_route(url.pathname + url.search);
        });
    });
    wrapper.workflow_designer = {page, frame};
    page.set_secondary_action(__("Close"), () => frappe.set_route(wrapper.workflow_designer.listHref || "/desk/automation-workflow"));
};

// Back links open the list inside the sidebar the designer was launched from.
function workflowListInSidebar(sidebar) {
    return sidebar && frappe.boot?.module_sidebars?.[sidebar]
        ? `/desk/${frappe.router.shell_slug(sidebar)}/automation-workflow`
        : "/desk/automation-workflow";
}

frappe.pages["automation-workflow-designer"].on_page_show = function (wrapper) {
    const [, name, sidebar] = frappe.get_route();
    if (!name) return frappe.set_route("List", "Automation Workflow");
    if (sidebar && frappe.boot?.module_sidebars?.[sidebar]) frappe.app?.sidebar?.select_shell(sidebar);
    const designer = wrapper.workflow_designer;
    designer.page.set_title(name);
    designer.listHref = workflowListInSidebar(sidebar);
    designer.page.set_breadcrumbs([
        {label: __("Workflows"), href: designer.listHref},
        {label: name, title: name},
    ]);
    if (designer.name === name) return;
    const open = () => {
        designer.name = name;
        designer.frame.src = `/workflow/${encodeURIComponent(name)}`;
    };
    // Reuse the editor's existing dirty-state control without modifying the React bundle.
    const save = designer.frame.contentDocument?.querySelector('button[title="Persist this draft revision"]');
    if (designer.name && save && !save.disabled) {
        frappe.confirm(__("Open another workflow? Save any pending changes in the current editor before continuing."), open, () => {
            frappe.set_route("automation-workflow-designer", designer.name, sidebar || "");
        });
    } else {
        open();
    }
};
