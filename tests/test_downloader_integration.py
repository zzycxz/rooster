# -*- coding: utf-8 -*-
"""
Tests for AriaNg Downloader UI integration in Rooster Dashboard.
"""

import os

DASHBOARD_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dashboard", "ui", "src", "dashboard.html")

I18N_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dashboard", "ui", "src", "js", "i18n.js")


def test_dashboard_file_exists():
    """Verify that dashboard.html exists."""
    assert os.path.exists(DASHBOARD_PATH), f"dashboard.html not found at {DASHBOARD_PATH}"


def test_downloader_tab_registered():
    """Verify that downloader tab is registered in Alpine.js tabs array."""
    with open(DASHBOARD_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    # Search for tabs registration of downloader
    assert "{ id: 'downloader', icon: '⬇️', label: 'Downloader' }" in content or "id: 'downloader'" in content, (
        "Downloader tab is not registered in the tabs array."
    )


def test_i18n_keys_in_dashboard():
    """Verify that downloader i18n translation keys exist in dashboard.html."""
    with open(DASHBOARD_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    # Verify English translations
    assert "tab_downloader:" in content, "Missing English key: tab_downloader"
    assert "downloader_desc:" in content, "Missing English key: downloader_desc"
    assert "open_new_window:" in content, "Missing English key: open_new_window"


def test_get_ariang_url_defined():
    """Verify that getAriaNgUrl method is implemented in dashboard.html."""
    with open(DASHBOARD_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    assert "getAriaNgUrl()" in content, "getAriaNgUrl method not defined in dashboard.html"
    assert "/ui/ariang/" in content, "AriaNg local base URL not found in getAriaNgUrl"


def test_iframe_downloader_exists():
    """Verify that the iframe element for AriaNg is embedded inside tab-downloader partial."""
    partials_path = os.path.join(os.path.dirname(DASHBOARD_PATH), "partials", "tab-downloader.html")
    with open(partials_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "activeTab === 'downloader'" in content, "Missing x-show activeTab === 'downloader' container"
    assert "<iframe" in content, "Missing iframe element inside tab-downloader.html"
    assert "getAriaNgUrl()" in content, "Iframe is not bound to getAriaNgUrl()"


def test_external_i18n_keys():
    """Verify that downloader i18n keys are correctly synchronized in dashboard/ui/src/js/i18n.js."""
    assert os.path.exists(I18N_PATH), f"i18n.js not found at {I18N_PATH}"

    with open(I18N_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    assert "tab_downloader:" in content, "Missing key in external i18n.js"
    assert "downloader_desc:" in content, "Missing description key in external i18n.js"
    assert "open_new_window:" in content, "Missing window key in external i18n.js"
