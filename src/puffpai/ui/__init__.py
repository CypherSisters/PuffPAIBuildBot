"""PuffPAI UI package — decomposed from app_ui.py monolith.

Exports ``PersonaRAGUI`` so that old ``from puffpai.ui import PersonaRAGUI``
works the same as ``from puffpai.ui.main_window import PersonaRAGUI``.
"""


def __getattr__(name):
    """Lazy import to avoid pulling tkinter at module scan time."""
    if name == "PersonaRAGUI":
        from .main_window import PersonaRAGUI
        return PersonaRAGUI
    if name == "KnowledgeView":
        from .knowledge_view import KnowledgeView
        return KnowledgeView
    if name == "DashboardView":
        from .dashboard_view import DashboardView
        return DashboardView
    if name == "DashboardTile":
        from .dashboard_tile import DashboardTile
        return DashboardTile
    if name == "UIPreferences":
        from .preferences import UIPreferences
        return UIPreferences
    if name == "SettingsView":
        from .settings_view import SettingsView
        return SettingsView
    if name == "OnboardingWizard":
        from .onboarding import OnboardingWizard
        return OnboardingWizard
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
