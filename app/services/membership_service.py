"""
MEMBERSHIP SERVICE
==================

Updated for withdrawal support and member dashboard.
"""

from datetime import datetime
from app.extensions import db
from app.models import (
    Group, GroupMember, AdminTransferHistory, WithdrawalRequest,
    MemberRole, LoanRequest, LoanStatus, MemberLedger, MemberFinancialSummary,
    WithdrawalStatus, GroupWallet
)
from app.services.authorization_service import (
    can_leave_group, can_transfer_admin,
    is_group_admin, AuthorizationError
)


class MembershipError(Exception):
    """Base exception for membership operations"""
    pass


# ============================================================
# ADD MEMBER
# ============================================================

def add_member(group_id, user_id, added_by_user_id, role=MemberRole.MEMBER.value):
    """Add a new member to group"""
    try:
        # Check if adder is admin
        if not is_group_admin(added_by_user_id, group_id):
            raise AuthorizationError("Only admin can add members")

        # Check if already active member
        existing = GroupMember.query.filter_by(
            group_id=group_id,
            user_id=user_id,
            is_active=True
        ).first()

        if existing:
            raise MembershipError("User is already an active member")

        # Check for inactive membership (rejoining)
        inactive_membership = GroupMember.query.filter_by(
            group_id=group_id,
            user_id=user_id,
            is_active=False
        ).first()

        if inactive_membership:
            # Reactivate
            inactive_membership.reactivate()
            inactive_membership.role = role
            membership = inactive_membership
        else:
            # Create new membership
            membership = GroupMember(
                group_id=group_id,
                user_id=user_id,
                role=role
            )
            db.session.add(membership)

        db.session.commit()

        return membership

    except (AuthorizationError, MembershipError):
        db.session.rollback()
        raise
    except Exception as e:
        db.session.rollback()
        raise MembershipError(f"Failed to add member: {str(e)}")


# ============================================================
# LEAVE GROUP (UPDATED)
# ============================================================

def leave_group(group_id, user_id, reason=None):
    """
    Member leaves group.

    STRICT RULES:
    - Cannot leave with active/unpaid loans
    - Admin must transfer rights first
    - MUST withdraw all contributions before leaving
    - No pending withdrawal requests
    """
    try:
        # Check authorization (includes liability check)
        allowed, error_reason = can_leave_group(user_id, group_id)
        if not allowed:
            raise AuthorizationError(error_reason)

        # Get membership
        membership = GroupMember.query.filter_by(
            group_id=group_id,
            user_id=user_id,
            is_active=True
        ).first()

        if not membership:
            raise MembershipError("You are not a member of this group")

        # Get wallet and ledger
        wallet = GroupWallet.query.filter_by(group_id=group_id).first()
        if not wallet:
            raise MembershipError("Group wallet not found")

        ledger = MemberLedger.query.filter_by(
            wallet_id=wallet.id,
            user_id=user_id
        ).first()

        # Check if member has contributions to withdraw
        if ledger and ledger.net_principal > 0:
            raise MembershipError(
                f"You must withdraw your contributions (₹{ledger.net_principal:.2f}) before leaving the group. "
                f"Please create a withdrawal request first."
            )

        # Soft delete
        membership.soft_delete(reason=reason or "Member left voluntarily")

        # Mark member's financial summary as dirty
        summary = MemberFinancialSummary.query.filter_by(user_id=user_id).first()
        if summary:
            summary.is_dirty = True

        db.session.commit()

        return True

    except (AuthorizationError, MembershipError):
        db.session.rollback()
        raise
    except Exception as e:
        db.session.rollback()
        raise MembershipError(f"Failed to leave group: {str(e)}")

# ============================================================
# REMOVE MEMBER (Admin action)
# ============================================================

def remove_member(group_id, user_id, removed_by_user_id, reason=None):
    """Admin removes a member from group"""
    try:
        # Check if remover is admin
        if not is_group_admin(removed_by_user_id, group_id):
            raise AuthorizationError("Only admin can remove members")

        # Cannot remove self
        if user_id == removed_by_user_id:
            raise MembershipError("Use 'leave group' to remove yourself")

        # Check if target has liabilities
        allowed, error_reason = can_leave_group(user_id, group_id)
        if not allowed:
            raise MembershipError(f"Cannot remove: {error_reason}")

        # Get membership
        membership = GroupMember.query.filter_by(
            group_id=group_id,
            user_id=user_id,
            is_active=True
        ).first()

        if not membership:
            raise MembershipError("User is not a member")

        # Soft delete
        membership.soft_delete(reason=reason or f"Removed by admin {removed_by_user_id}")

        # Mark member's financial summary as dirty
        summary = MemberFinancialSummary.query.filter_by(user_id=user_id).first()
        if summary:
            summary.is_dirty = True

        db.session.commit()

        return True

    except (AuthorizationError, MembershipError):
        db.session.rollback()
        raise
    except Exception as e:
        db.session.rollback()
        raise MembershipError(f"Failed to remove member: {str(e)}")


# ============================================================
# TRANSFER ADMIN RIGHTS
# ============================================================

def transfer_admin(group_id, from_user_id, to_user_id, reason=None):
    """
    Transfer admin rights to another member.

    Admin cannot leave without transferring first.
    System ensures at least one admin exists.
    """
    try:
        # Check authorization
        allowed, error_reason = can_transfer_admin(from_user_id, to_user_id, group_id)
        if not allowed:
            raise AuthorizationError(error_reason)

        # Get memberships
        from_membership = GroupMember.query.filter_by(
            group_id=group_id,
            user_id=from_user_id,
            is_active=True
        ).first()

        to_membership = GroupMember.query.filter_by(
            group_id=group_id,
            user_id=to_user_id,
            is_active=True
        ).first()

        # Transfer roles
        from_membership.role = MemberRole.MEMBER.value
        to_membership.role = MemberRole.ADMIN.value

        # Create audit record
        transfer_record = AdminTransferHistory(
            group_id=group_id,
            from_user_id=from_user_id,
            to_user_id=to_user_id,
            reason=reason
        )
        db.session.add(transfer_record)

        db.session.commit()

        return transfer_record

    except AuthorizationError:
        db.session.rollback()
        raise
    except Exception as e:
        db.session.rollback()
        raise MembershipError(f"Failed to transfer admin: {str(e)}")


# ============================================================
# GET MEMBER LIABILITIES (UPDATED)
# ============================================================

def get_member_liabilities(user_id, group_id):
    """
    Get all outstanding liabilities for a member.
    Used to explain why they cannot leave.
    """
    liabilities = {
        'can_leave': True,
        'reasons': [],
        'pending_loans': [],
        'active_loans': [],
        'pending_repayments': [],
        'pending_withdrawals': []
    }

    # Check pending withdrawal requests
    pending_withdrawals = WithdrawalRequest.query.filter_by(
        user_id=user_id,
        group_id=group_id,
        status=WithdrawalStatus.PENDING.value
    ).all()

    if pending_withdrawals:
        liabilities['can_leave'] = False
        liabilities['reasons'].append("You have pending withdrawal requests")
        liabilities['pending_withdrawals'] = [
            {'id': w.id, 'amount': w.total_amount}
            for w in pending_withdrawals
        ]

    # Check pending loan requests
    pending_loans = LoanRequest.query.filter_by(
        group_id=group_id,
        requested_by=user_id,
        status=LoanStatus.PENDING.value,
        is_active=True
    ).all()

    if pending_loans:
        liabilities['can_leave'] = False
        liabilities['reasons'].append("You have pending loan requests")
        liabilities['pending_loans'] = [
            {'id': l.id, 'amount': l.amount}
            for l in pending_loans
        ]

    # Check approved/disbursed loans
    active_loans = LoanRequest.query.filter(
        LoanRequest.group_id == group_id,
        LoanRequest.requested_by == user_id,
        LoanRequest.is_active == True,
        LoanRequest.status.in_([
            LoanStatus.APPROVED.value,
            LoanStatus.DISBURSED.value
        ])
    ).all()

    for loan in active_loans:
        remaining = loan.get_remaining_amount()
        if remaining > 0:
            liabilities['can_leave'] = False
            liabilities['reasons'].append(f"Outstanding loan: ₹{remaining:.2f}")
            liabilities['active_loans'].append({
                'id': loan.id,
                'amount': loan.approved_amount,
                'remaining': remaining
            })

    # Check pending repayments
    from app.models import LoanRepayment, RepaymentStatus
    pending_repayments = LoanRepayment.query.join(LoanRequest).filter(
        LoanRequest.group_id == group_id,
        LoanRepayment.paid_by == user_id,
        LoanRepayment.status == RepaymentStatus.PENDING.value
    ).all()

    if pending_repayments:
        liabilities['can_leave'] = False
        liabilities['reasons'].append("You have pending repayments awaiting approval")
        liabilities['pending_repayments'] = [
            {'id': r.id, 'amount': r.amount}
            for r in pending_repayments
        ]

    return liabilities


# ============================================================
# GET MEMBER FINANCIAL SUMMARY (NEW)
# ============================================================

def get_member_group_financial_summary(user_id, group_id):
    """
    Get member's financial summary for a specific group.
    """
    wallet = GroupWallet.query.filter_by(group_id=group_id).first()
    if not wallet:
        return {"error": "Group wallet not found"}

    ledger = MemberLedger.query.filter_by(
        wallet_id=wallet.id,
        user_id=user_id
    ).first()

    if not ledger:
        return {
            'has_ledger': False,
            'is_active_member': GroupMember.query.filter_by(
                group_id=group_id,
                user_id=user_id,
                is_active=True
            ).first() is not None
        }

    membership = GroupMember.query.filter_by(
        group_id=group_id,
        user_id=user_id,
        is_active=True
    ).first()

    # Get withdrawal history
    withdrawals = WithdrawalRequest.query.filter_by(
        user_id=user_id,
        group_id=group_id
    ).order_by(WithdrawalRequest.created_at.desc()).all()

    return {
        'has_ledger': True,
        'is_active_member': membership is not None,
        'membership_role': membership.role if membership else None,
        'ledger_summary': ledger.get_dashboard_summary(),
        'withdrawal_history': [
            {
                'id': w.id,
                'amount': w.total_amount,
                'principal': w.principal_amount,
                'interest': w.interest_amount,
                'status': w.status,
                'created_at': w.created_at,
                'processed_at': w.processed_at,
                'membership_action': w.membership_action
            }
            for w in withdrawals
        ]
    }


# ============================================================
# REJOIN GROUP (NEW)
# ============================================================

def rejoin_group(user_id, group_id, contribution_amount=0):
    """
    Re-join a group after withdrawal.

    This function is also in models.py, but included here for service consistency.
    """
    # Check if already active member
    existing = GroupMember.query.filter_by(
        group_id=group_id,
        user_id=user_id,
        is_active=True
    ).first()

    if existing:
        raise ValueError("User is already an active member of this group")

    # Find inactive membership
    inactive_membership = GroupMember.query.filter_by(
        group_id=group_id,
        user_id=user_id,
        is_active=False
    ).first()

    if inactive_membership:
        # Reactivate
        inactive_membership.reactivate()
    else:
        # Create new membership
        inactive_membership = GroupMember(
            group_id=group_id,
            user_id=user_id,
            role='member'
        )
        db.session.add(inactive_membership)

    # If contributing new money
    if contribution_amount > 0:
        from app.services.wallet_service import contribute_to_wallet
        wallet = GroupWallet.query.filter_by(group_id=group_id).first()
        if wallet:
            contribute_to_wallet(
                wallet_id=wallet.id,
                user_id=user_id,
                amount=contribution_amount,
                description="Re-joining contribution"
            )

    # Update member's financial summary
    summary = MemberFinancialSummary.query.filter_by(user_id=user_id).first()
    if summary:
        summary.is_dirty = True

    db.session.commit()

    return inactive_membership