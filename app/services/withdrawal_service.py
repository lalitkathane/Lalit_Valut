# =================
# WITHDRAWAL SERVICE
# ==================

from datetime import datetime
from app.extensions import db
from app.models import WithdrawalRequest, WithdrawalStatus
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

def create_withdrawal_request(user_id, group_id, total_amount, membership_action='deactivate', reason=None):
    try:
        # Check authorization - this already checks:
        # 1. Membership
        # 2. Active loans (using get_active_loans)
        # 3. Wallet exists
        # 4. Ledger exists with balance > 0
        # 5. No pending withdrawals
        allowed, reason_msg = can_withdraw(user_id, group_id)
        if not allowed:
            raise AuthorizationError(reason_msg)

        # Get wallet and ledger for amount validations
        # (Using helper functions for consistency)
        wallet = get_group_wallet(group_id)
        ledger = get_user_ledger_for_group(user_id, group_id, active_only=True)

        # Validate amounts
        if total_amount <= 0:
            raise WithdrawalError("Amount must be greater than 0")

        # Check if amount exceeds total available balance
        if total_amount > ledger.total_balance:
            raise WithdrawalError(f"Insufficient balance. Available: ₹{ledger.total_balance}")

        # Check group balance
        if wallet.balance < total_amount:
            raise WithdrawalError(
                f"Insufficient group balance. Required: ₹{total_amount}, Available: ₹{wallet.balance}")

        # Calculate principal and interest amounts proportionally
        if ledger.total_balance > 0:
            principal_ratio = ledger.net_principal / ledger.total_balance
            interest_ratio = ledger.net_interest / ledger.total_balance

            principal_amount = round(total_amount * principal_ratio, 2)
            interest_amount = round(total_amount * interest_ratio, 2)

            # Adjust for rounding errors
            if principal_amount + interest_amount != total_amount:
                principal_amount = total_amount - interest_amount
        else:
            principal_amount = 0
            interest_amount = 0

        # Create withdrawal request with reason
        withdrawal = WithdrawalRequest(
            user_id=user_id,
            group_id=group_id,
            principal_amount=principal_amount,
            interest_amount=interest_amount,
            total_amount=total_amount,
            status=WithdrawalStatus.PENDING.value,
            membership_action=membership_action,
            withdrawal_reason=reason,  # Store member's reason here
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
            "total_available": 0,
            "group_balance": wallet.balance
        }

    # Check if member has active loans using helper function
    active_loans = get_active_loans(user_id, group_id)

    # If member has active loans, they cannot withdraw
    if active_loans:
        return {
            "total_available": 0,
            "group_balance": wallet.balance,
            "member_net_principal": ledger.net_principal,
            "member_net_interest": ledger.net_interest,
            "member_total_balance": ledger.total_balance,
            "has_active_loans": True,
            "message": "Cannot withdraw while you have active or pending loans"
        }

    # Can't withdraw more than group has
    total_available = min(ledger.total_balance, wallet.balance)

    return {
        "total_available": total_available,
        "group_balance": wallet.balance,
        "member_net_principal": ledger.net_principal,
        "member_net_interest": ledger.net_interest,
        "member_total_balance": ledger.total_balance,
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

def validate_withdrawal_amount(user_id, group_id, total_amount):
    """
    Validate withdrawal amount before creating a request.

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

    # Validate amount
    if total_amount <= 0:
        return False, "Amount must be greater than 0"

    if total_amount > ledger.total_balance:
        return False, f"Insufficient balance. Available: ₹{ledger.total_balance}"

    if wallet.balance < total_amount:
        return False, f"Insufficient group balance. Required: ₹{total_amount}, Available: ₹{wallet.balance}"

    return True, "Amount is valid"


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