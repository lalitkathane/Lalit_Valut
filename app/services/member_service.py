from datetime import datetime
from app.extensions import db
from app.models import  Group,MemberFinancialSummary
from app.services.helperfunctions import *

# ============================================================
# GET MEMBER DASHBOARD
# ============================================================

def get_member_dashboard(user_id):
    """
    Get comprehensive dashboard for a member across all groups.
    """
    # Get or create financial summary
    summary = MemberFinancialSummary.query.filter_by(user_id=user_id).first()
    if not summary:
        summary = MemberFinancialSummary(user_id=user_id)
        db.session.add(summary)
        db.session.commit()

    if summary.is_dirty:
        summary.recalculate_from_ledgers()
        db.session.commit()
        summary = MemberFinancialSummary.query.filter_by(user_id=user_id).first()

    # Get all memberships using helper
    memberships = get_user_memberships(user_id, is_active=True)

    # Get all ledgers using helper
    ledgers = get_user_ledgers(user_id)

    dashboard = {
        'user_id': user_id,
        'financial_summary': summary.get_summary(),
        'total_across_all_groups': {
            'principal_contributed': 0,
            'principal_withdrawn': 0,
            'net_principal': 0,
            'interest_earned': 0,
            'interest_withdrawn': 0,
            'net_interest': 0,
            'total_balance': 0
        },
        'active_groups': [],
        'inactive_groups': [],
        'loans_summary': {
            'borrowed': get_member_loans_summary(user_id),
            'lent': get_member_lending_summary(user_id)
        }
    }

    for ledger in ledgers:
        group_info = ledger.get_dashboard_summary()

        if group_info['is_active_member']:
            dashboard['active_groups'].append(group_info)
        else:
            dashboard['inactive_groups'].append(group_info)

        # Update totals
        dashboard['total_across_all_groups']['principal_contributed'] += ledger.principal_contributed
        dashboard['total_across_all_groups']['principal_withdrawn'] += ledger.principal_withdrawn
        dashboard['total_across_all_groups']['net_principal'] += ledger.net_principal
        dashboard['total_across_all_groups']['interest_earned'] += ledger.interest_earned
        dashboard['total_across_all_groups']['interest_withdrawn'] += ledger.interest_withdrawn
        dashboard['total_across_all_groups']['net_interest'] += ledger.net_interest
        dashboard['total_across_all_groups']['total_balance'] += ledger.total_balance

    # Add group details for active memberships without ledger (new members)
    for membership in memberships:
        # Check if ledger exists for this group
        ledger_exists = any(l.wallet.group_id == membership.group_id for l in ledgers)
        if not ledger_exists:
            group = Group.query.get(membership.group_id)
            dashboard['active_groups'].append({
                'group_id': group.id,
                'group_name': group.name,
                'principal_contributed': 0,
                'principal_withdrawn': 0,
                'net_principal': 0,
                'interest_earned': 0,
                'interest_withdrawn': 0,
                'net_interest': 0,
                'total_balance': 0,
                'total_principal_ever': 0,
                'total_interest_ever': 0,
                'is_active_member': True
            })

    return dashboard


# ============================================================
# GET MEMBER LOANS SUMMARY
# ============================================================

def get_member_loans_summary(user_id):
    """
    Get summary of loans taken by member.
    """
    # Loans where member is borrower using helper
    loan_requests = get_user_loans(user_id, is_active=True)

    summary = {
        'total_loans': len(loan_requests),
        'total_borrowed': 0,
        'total_repaid': 0,
        'total_remaining': 0,
        'active_loans': [],
        'completed_loans': []
    }

    for loan in loan_requests:
        loan_data = {
            'id': loan.id,
            'group_id': loan.group_id,
            'group_name': loan.group.name,
            'amount': loan.amount,
            'approved_amount': loan.approved_amount,
            'status': loan.status,
            'total_repayable': loan.total_repayable,
            'total_repaid': loan.total_repaid,
            'remaining': loan.get_remaining_amount(),
            'interest_rate': loan.interest_rate,
            'emi_amount': loan.emi_amount,
            'approved_at': loan.approved_at,
            'disbursed_at': loan.disbursed_at
        }

        summary['total_borrowed'] += loan.approved_amount or loan.amount
        summary['total_repaid'] += loan.total_repaid
        summary['total_remaining'] += loan.get_remaining_amount()

        if loan.status in ['disbursed', 'approved']:
            summary['active_loans'].append(loan_data)
        elif loan.status == 'completed':
            summary['completed_loans'].append(loan_data)

    return summary


# ============================================================
# GET MEMBER LENDING SUMMARY
# ============================================================

def get_member_lending_summary(user_id):
    """
    Get summary of lending (interest earned from other members' loans).
    """
    # Get interest distributions to this member using helper
    interest_distributions = get_interest_distributions_to_user(user_id)

    summary = {
        'total_interest_earned': 0,
        'total_loans_contributed_to': 0,
        'distributions': []
    }

    loans_contributed = set()

    for dist in interest_distributions:
        summary['total_interest_earned'] += dist.interest_earned
        loans_contributed.add(dist.loan_id)

        summary['distributions'].append({
            'loan_id': dist.loan_id,
            'repayment_id': dist.repayment_id,
            'interest_earned': dist.interest_earned,
            'contribution_percentage': dist.contribution_percentage,
            'created_at': dist.created_at
        })

    summary['total_loans_contributed_to'] = len(loans_contributed)

    return summary


# ============================================================
# GET MEMBER WITHDRAWAL HISTORY
# ============================================================

def get_member_withdrawal_history(user_id):
    """
    Get withdrawal history for a member across all groups.
    """
    # Get all withdrawals using helper
    withdrawals = get_user_withdrawals(user_id)

    history = []

    for withdrawal in withdrawals:
        group = Group.query.get(withdrawal.group_id)
        history.append({
            'id': withdrawal.id,
            'group_id': withdrawal.group_id,
            'group_name': group.name if group else 'Unknown Group',
            'principal_amount': withdrawal.principal_amount,
            'interest_amount': withdrawal.interest_amount,
            'total_amount': withdrawal.total_amount,
            'status': withdrawal.status,
            'withdrawal_type': withdrawal.withdrawal_type,
            'membership_action': withdrawal.membership_action,
            'created_at': withdrawal.created_at,
            'approved_at': withdrawal.approved_at,
            'processed_at': withdrawal.processed_at
        })

    return history


# ============================================================
# GET MEMBER'S GROUP FINANCIAL DETAILS
# ============================================================

def get_member_group_details(user_id, group_id):
    """
    Get detailed financial information for a member in a specific group.
    """
    # Get group wallet using helper
    wallet = get_group_wallet(group_id)
    if not wallet:
        return {"error": "Group wallet not found"}

    # Get member ledger using helper
    ledger = get_user_ledger_for_group(user_id, group_id)

    if not ledger:
        return {
            'has_ledger': False,
            'group_name': wallet.group.name,
            'group_balance': wallet.balance
        }

    # Get contributions using helper
    contributions = get_user_contributions(user_id, wallet.id)

    # Get interest distributions using helper
    interest_distributions = get_interest_distributions_to_user(user_id, group_id)

    # Get loans as borrower using helper
    loans_as_borrower = get_user_loans_for_group(user_id, group_id)

    return {
        'has_ledger': True,
        'group_name': wallet.group.name,
        'group_balance': wallet.balance,
        'ledger_summary': ledger.get_dashboard_summary(),
        'contributions': [
            {
                'id': c.id,
                'amount': c.amount,
                'description': c.description,
                'contributed_at': c.contributed_at
            }
            for c in contributions
        ],
        'interest_earnings': [
            {
                'loan_id': i.loan_id,
                'amount': i.interest_earned,
                'percentage': i.contribution_percentage,
                'created_at': i.created_at
            }
            for i in interest_distributions
        ],
        'loans_as_borrower': [
            {
                'id': l.id,
                'amount': l.amount,
                'approved_amount': l.approved_amount,
                'status': l.status,
                'total_repaid': l.total_repaid,
                'remaining': l.get_remaining_amount(),
                'created_at': l.created_at
            }
            for l in loans_as_borrower
        ]
    }