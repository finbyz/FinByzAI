# Copyright (c) 2026, Finbyz Tech Pvt Ltd and contributors
# For license information, please see license.txt

"""Tells the desk whether to mount the Copilot at all.

`app_include_js` ships the bundle to every user on the site, so the decision about
who actually gets the Copilot has to travel with the boot payload. Without this the
panel mounts for everyone, calls its endpoints, and a user who was never given the
feature is greeted by a permission dialog for a doctype they have never heard of.
"""

import frappe

from finbyzai.copilot import access


def boot_session(bootinfo) -> None:
    try:
        bootinfo.copilot_enabled = access.has_copilot()
        bootinfo.copilot_admin = access.is_copilot_admin()
    except Exception:
        # Boot must never fail because of this; a missing flag reads as "off".
        bootinfo.copilot_enabled = False
        bootinfo.copilot_admin = False
