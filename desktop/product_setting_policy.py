"""Exact current preference families. Dynamic task IDs are one family."""
ACCOUNT_SYNC_SETTINGS = (
    'display_name','locale','timezone','theme','learning_goal','daily_minutes',
    'notifications_enabled','default_workspace_view','analogy/keywords','task-colors/{workspace_id}',
)
DEVICE_LOCAL_SETTINGS = (
    'account/profile_mode','account/issuer_hash','account/subject_hash','account/display_name',
    'account/sync_cursor','account/model_selection','account/remember_me','account/auto_login',
    'account/registry_v1',
    'tutorial/step','tutorial/completed','appearance/classroom_theme',
    'analogy/generation_policy','analogy/variation_counter','analogy/recent_cards',
    'analogy/latest_card','analogy/prefetched_card','analogy/card_library','news/interests',
    'official_updates/enabled','provider/base_url','provider/model','provider/preset_name',
    'provider/credential_ref','classroom/free_chat_prompt','development/teacher_action_reviews_v1',
    'updater/{install_identity}/{window}/geometry','updater/{install_identity}/navigation',
)
# Account display_name is an editable domain preference. account/display_name is
# a local projection of verified login identity; preferences never authorize it.
# Appearance/classroom_theme selects local rendering; theme is the account's
# domain preference. Free-chat prompt is explicitly local in the existing UI.
