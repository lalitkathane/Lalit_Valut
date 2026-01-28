# =================
# WITHDRAWAL SERVICE
# ==================

from datetime import datetime
from app.extensions import db
from app.models import  WithdrawalRequest, WithdrawalStatus
from app.services.authorization_service import (
    can_withdraw, can_approve_withdrawal, is_group_admin, AuthorizationError
)
import uuid
# Import helper functions
from app.services.helperfunctions import *


class WithdrawalError(Exception):
    """Base exception for withdrawal operations"""
    pass

# ============================================================
# CREATE WITHDRAWAL REQUEST
# ============================================================

def create_withdrawal_request(user_id, group_id, principal_amount, interest_amount=0, withdrawal_type='principal_only',
                              membership_action='deactivate'):
    try:
        # Check authorization - this already checks:
        # 1. Membership
        # 2. Active loans (using get_active_loans)
        # 3. Wallet exists
        # 4. Ledger exists with balance > 0
        # 5. No pending withdrawals
        allowed, reason = can_withdraw(user_id, group_id)
        if not allowed:
            raise AuthorizationError(reason)

        # NO NEED for duplicate loan check here - can_withdraw() already checked it
        # NO NEED for duplicate wallet check here - can_withdraw() already checked it
        # NO NEED for duplicate ledger check here - can_withdraw() already checked it

        # Get wallet and ledger for amount validations
        # (Using helper functions for consistency)
        wallet = get_group_wallet(group_id)
        ledger = get_user_ledger_for_group(user_id, group_id, active_only=True)

        # Wallet and ledger are guaranteed to exist at this point because can_withdraw() passed
        # But we still need them for amount validations

        # Validate amounts (NEW checks not in can_withdraw())
        if principal_amount <= 0:
            raise WithdrawalError("Principal amount must be greater than 0")

        if principal_amount > ledger.net_principal:
            raise WithdrawalError(f"Insufficient principal balance. Available: ₹{ledger.net_principal}")

        if interest_amount > ledger.net_interest:
            raise WithdrawalError(f"Insufficient interest balance. Available: ₹{ledger.net_interest}")

        total_amount = principal_amount + interest_amount

        # Check group balance (NEW check not in can_withdraw())
        if wallet.balance < total_amount:
            raise WithdrawalError(
                f"Insufficient group balance. Required: ₹{total_amount}, Available: ₹{wallet.balance}")

        # Create withdrawal request
        withdrawal = WithdrawalRequest(
            user_id=user_id,
            group_id=group_id,
            withdrawal_type=withdrawal_type,
            principal_amount=principal_amount,
            interest_amount=interest_amount,
            total_amount=total_amount,
            status=WithdrawalStatus.PENDING.value,
            membership_action=membership_action,
            idempotency_key=f"withdraw_{user_id}_{group_id}_{uuid.uuid4().hex[:16]}"
        )

        db.session.add(withdrawal)
        db.session.commit()

        return withdrawal

    except (AuthorizationError, WithdrawalError):
        db.session.rollback()
        raise
    except Exception as e:
        db.session.rollback()
        raise WithdrawalError(f"Failed to create withdrawal request: {str(e)}")


# ============================================================
# APPROVE WITHDRAWAL
# ============================================================

def approve_withdrawal(withdrawal_id, admin_user_id):
    """
    Approve a withdrawal request.

    Returns: Updated WithdrawalRequest
    """
    try:
        # Check authorization
        allowed, reason = can_approve_withdrawal(admin_user_id, withdrawal_id)
        if not allowed:
            raise AuthorizationError(reason)

        withdrawal = WithdrawalRequest.query.get(withdrawal_id)
        if not withdrawal:
            raise WithdrawalError("Withdrawal request not found")

        # Check for active loans using helper function
        active_loans = get_active_loans(withdrawal.user_id, withdrawal.group_id)

        if active_loans:
            error_message = check_loans_and_get_message(
                active_loans,
                user_type="Member",
                context_action="approve withdrawal"
            )
            if error_message:
                raise WithdrawalError(error_message)

        # Approve the request
        withdrawal.approve(admin_user_id)

        # Process the withdrawal (updates wallet, ledger, etc.)
        withdrawal.process_withdrawal()

        db.session.commit()

        return withdrawal

    except (AuthorizationError, WithdrawalError):
        db.session.rollback()
        raise
    except Exception as e:
        db.session.rollback()
        raise WithdrawalError(f"Failed to approve withdrawal: {str(e)}")


# ============================================================
# REJECT WITHDRAWAL
# ============================================================

def reject_withdrawal(withdrawal_id, admin_user_id, reason=None):
    """
    Reject a withdrawal request.

    Returns: Updated WithdrawalRequest
    """
    try:
        withdrawal = WithdrawalRequest.query.get(withdrawal_id)
        if not withdrawal:
            raise WithdrawalError("Withdrawal request not found")

        if withdrawal.status != WithdrawalStatus.PENDING.value:
            raise WithdrawalError(f"Cannot reject withdrawal in {withdrawal.status} status")

        if not is_group_admin(admin_user_id, withdrawal.group_id):
            raise AuthorizationError("Only group admin can reject withdrawals")

        # Reject the request
        withdrawal.reject(admin_user_id, reason)

        db.session.commit()

        return withdrawal

    except (AuthorizationError, WithdrawalError):
        db.session.rollback()
        raise
    except Exception as e:
        db.session.rollback()
        raise WithdrawalError(f"Failed to reject withdrawal: {str(e)}")


# ============================================================
# GET WITHDRAWAL HISTORY
# ============================================================

def get_withdrawal_history(user_id=None, group_id=None, status=None):
    """
    Get withdrawal history with filters.

    Args:
        user_id: Filter by user
        group_id: Filter by group
        status: Filter by status

    Returns: List of WithdrawalRequest
    """
    query = WithdrawalRequest.query

    if user_id:
        query = query.filter_by(user_id=user_id)

    if group_id:
        query = query.filter_by(group_id=group_id)

    if status:
        query = query.filter_by(status=status)

    query = query.order_by(WithdrawalRequest.created_at.desc())

    return query.all()


# ============================================================
# GET WITHDRAWABLE AMOUNTS
# ============================================================

def get_withdrawable_amounts(user_id, group_id):
    """
    Get how much a member can withdraw from a group.

    Returns: Dict with withdrawable amounts
    """
    wallet = get_group_wallet(group_id)
    if not wallet:
        return {"error": "Group wallet not found"}

    ledger = get_user_ledger_for_group(user_id, group_id, active_only=True)

    if not ledger:
        return {
            "principal_available": 0,
            "interest_available": 0,
            "total_available": 0,
            "group_balance": wallet.balance
        }

    # Check if member has active loans using helper function
    active_loans = get_active_loans(user_id, group_id)

    # If member has active loans, they cannot withdraw
    if active_loans:
        return {
            "principal_available": 0,
            "interest_available": 0,
            "total_available": 0,
            "group_balance": wallet.balance,
            "member_net_principal": ledger.net_principal,
            "member_net_interest": ledger.net_interest,
            "has_active_loans": True,
            "message": "Cannot withdraw while you have active or pending loans"
        }

    # Can't withdraw more than group has
    principal_available = min(ledger.net_principal, wallet.balance)
    interest_available = ledger.net_interest
    total_available = principal_available + interest_available

    return {
        "principal_available": principal_available,
        "interest_available": interest_available,
        "total_available": total_available,
        "group_balance": wallet.balance,
        "member_net_principal": ledger.net_principal,
        "member_net_interest": ledger.net_interest,
        "has_active_loans": False
    }


# ============================================================
# CANCEL WITHDRAWAL REQUEST
# ============================================================

def cancel_withdrawal_request(withdrawal_id, user_id):
    """
    Cancel a pending withdrawal request.

    Returns: Updated WithdrawalRequest
    """
    try:
        withdrawal = WithdrawalRequest.query.get(withdrawal_id)
        if not withdrawal:
            raise WithdrawalError("Withdrawal request not found")

        if withdrawal.user_id != user_id:
            raise AuthorizationError("You can only cancel your own withdrawal requests")

        if withdrawal.status != WithdrawalStatus.PENDING.value:
            raise WithdrawalError(f"Cannot cancel withdrawal in {withdrawal.status} status")

        # Soft delete the withdrawal
        withdrawal.status = 'cancelled'
        withdrawal.deleted_at = datetime.utcnow()

        db.session.commit()

        return withdrawal

    except (AuthorizationError, WithdrawalError):
        db.session.rollback()
        raise
    except Exception as e:
        db.session.rollback()
        raise WithdrawalError(f"Failed to cancel withdrawal: {str(e)}")


# ============================================================
# ADDITIONAL HELPER FUNCTIONS FOR WITHDRAWAL SERVICE
# ============================================================

def validate_withdrawal_amounts(user_id, group_id, principal_amount, interest_amount=0):
    """
    Validate withdrawal amounts before creating a request.

    Returns: Tuple (is_valid, error_message)
    """
    # Get wallet and ledger using helper functions
    wallet = get_group_wallet(group_id)
    if not wallet:
        return False, "Group wallet not found"

    ledger = get_user_ledger_for_group(user_id, group_id, active_only=True)
    if not ledger:
        return False, "Member ledger not found"

    # Check active loans
    active_loans = get_active_loans(user_id, group_id)
    if active_loans:
        error_message = check_loans_and_get_message(
            active_loans,
            user_type="You",
            context_action="withdraw"
        )
        return False, error_message

    # Validate amounts
    if principal_amount <= 0:
        return False, "Principal amount must be greater than 0"

    if principal_amount > ledger.net_principal:
        return False, f"Insufficient principal balance. Available: ₹{ledger.net_principal}"

    if interest_amount > ledger.net_interest:
        return False, f"Insufficient interest balance. Available: ₹{ledger.net_interest}"

    total_amount = principal_amount + interest_amount

    if wallet.balance < total_amount:
        return False, f"Insufficient group balance. Required: ₹{total_amount}, Available: ₹{wallet.balance}"

    return True, "Amounts are valid"


def get_pending_withdrawal_for_user(user_id, group_id):
    """
    Get pending withdrawal request for a user in a group.

    Returns: WithdrawalRequest or None
    """
    # Check if user has pending withdrawals using helper function
    if has_pending_withdrawals(user_id, group_id):
        return WithdrawalRequest.query.filter_by(
            user_id=user_id,
            group_id=group_id,
            status=WithdrawalStatus.PENDING.value
        ).first()
    return None

