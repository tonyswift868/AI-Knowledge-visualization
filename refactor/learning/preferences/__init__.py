def create_page(context, parent=None):
    from .page import PreferencesPage
    return PreferencesPage(context, parent)
