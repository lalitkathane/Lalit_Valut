"""
Template Context Processors
Make helper functions available in all templates
"""
from app.services.authorization_service import is_group_admin, is_group_member
from app.services.membership_service import get_member_liabilities

def auth_context_processor():
    """Make authorization functions available in templates"""
    return {
        'is_group_admin': is_group_admin,
        'is_group_member': is_group_member,
        'get_member_liabilities': get_member_liabilities
    }