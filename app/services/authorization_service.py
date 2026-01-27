"""
CENTRALIZED AUTHORIZATION SERVICE
==================================

Updated for new withdrawal and member dashboard features.
"""

from app.models import (
    User, Group, GroupMember, GroupWallet, LoanRequest, LoanRepayment,
    MemberLedger, LoanStatus, RepaymentStatus, MemberRole, WithdrawalRequest, WithdrawalStatus
)
from app.extensions import db


class AuthorizationError(Exception):
    """Raised when authorization fails"""
    pass


# ============================================================
# GROUP MEMBERSHIP CHECKS
# ============================================================

def is_group_member(user_id, group_id):
    """Check if user is an active member of group"""
    membership = GroupMember.query.filter_by(
        user_id=user_id,
        group_id=group_id,
        is_active=True
    ).first()
    return membership is not None


def is_group_admin(user_id, group_id):
    """Check if user is an active admin of group"""
    membership = GroupMember.query.filter_by(
        user_id=user_id,
        group_id=group_id,
        is_active=True
    ).first()
    return membership and membership.role == MemberRole.ADMIN.value


def get_membership(user_id, group_id):
    """Get active membership record"""
    return GroupMember.query.filter_by(
        user_id=user_id,
        group_id=group_id,
        is_active=True
    ).first()


# ============================================================
# CONTRIBUTION AUTHORIZATION
# ============================================================

def can_contribute(user_id, wallet_id):
    """
    Check if user can contribute to wallet.

    Requirements:
    - User must be active member of the group
    """
    wallet = GroupWallet.query.get(wallet_id)
    if not wallet:
        return False, "Wallet not found"

    if not is_group_member(user_id, wallet.group_id):
        return False, "You are not a member of this group"

    return True, None


# ============================================================
# WITHDRAWAL AUTHORIZATION (NEW)
# ============================================================

def can_withdraw(user_id, group_id):
    """
    Check if user can withdraw from group.

    Requirements:
    - User must be active member
    - User must have positive net principal balance
    - No pending withdrawal requests
    """
    # Check membership
    membership = get_membership(user_id, group_id)
    if not membership:
        return False, "You are not an active member of this group"

    # Check wallet and ledger
    wallet = GroupWallet.query.filter_by(group_id=group_id).first()
    if not wallet:
        return False, "Group wallet not found"

    ledger = MemberLedger.query.filter_by(
        wallet_id=wallet.id,
        user_id=user_id,
        is_active=True
    ).first()

    if not ledger or ledger.net_principal <= 0:
        return False, "You have no withdrawable principal balance"

    # Check for pending withdrawal requests
    pending_withdrawals = WithdrawalRequest.query.filter_by(
        user_id=user_id,
        group_id=group_id,
        status=WithdrawalStatus.PENDING.value
    ).count()

    if pending_withdrawals > 0:
        return False, "You already have a pending withdrawal request"

    return True, None


def can_approve_withdrawal(user_id, withdrawal_id):
    """
    Check if user can approve a withdrawal request.

    Requirements:
    - User must be group admin
    - Withdrawal must be in PENDING status
    """
    withdrawal = WithdrawalRequest.query.get(withdrawal_id)
    if not withdrawal:
        return False, "Withdrawal request not found"

    if withdrawal.status != WithdrawalStatus.PENDING.value:
        return False, f"Withdrawal is already {withdrawal.status}"

    if not is_group_admin(user_id, withdrawal.group_id):
        return False, "Only group admin can approve withdrawals"

    # Check if group has sufficient balance
    wallet = GroupWallet.query.filter_by(group_id=withdrawal.group_id).first()
    if wallet.balance < withdrawal.total_amount:
        return False, f"Insufficient group balance. Required: ₹{withdrawal.total_amount}, Available: ₹{wallet.balance}"

    # Check if member still has sufficient balance
    ledger = MemberLedger.query.filter_by(
        wallet_id=wallet.id,
        user_id=withdrawal.user_id
    ).first()

    if not ledger:
        return False, "Member ledger not found"

    if withdrawal.principal_amount > ledger.net_principal:
        return False, f"Insufficient principal balance. Requested: ₹{withdrawal.principal_amount}, Available: ₹{ledger.net_principal}"

    if withdrawal.interest_amount > ledger.net_interest:
        return False, f"Insufficient interest balance. Requested: ₹{withdrawal.interest_amount}, Available: ₹{ledger.net_interest}"

    return True, None


# ============================================================
# LOAN VOTING AUTHORIZATION
# ============================================================

def can_vote(user_id, loan_id):
    """
    Check if user can vote on loan.

    Requirements:
    - Loan must be in PENDING status
    - User must be active member of the group
    - User cannot vote on own loan
    - User must not have already voted
    """
    loan = LoanRequest.query.get(loan_id)
    if not loan:
        return False, "Loan not found"

    if not loan.is_active:
        return False, "This loan request is no longer active"

    if loan.status != LoanStatus.PENDING.value:
        return False, f"Voting is closed. Loan is {loan.status}"

    if not is_group_member(user_id, loan.group_id):
        return False, "You are not a member of this group"

    if loan.requested_by == user_id:
        return False, "You cannot vote on your own loan request"

    # Check if already voted
    from app.models import LoanApproval
    existing_vote = LoanApproval.query.filter_by(
        loan_id=loan_id,
        user_id=user_id
    ).first()

    if existing_vote:
        return False, "You have already voted on this loan"

    return True, None


# ============================================================
# LOAN DISBURSEMENT AUTHORIZATION
# ============================================================

def can_disburse(user_id, loan_id):
    """
    Check if user can disburse loan.

    Requirements:
    - User must be group admin
    - Loan must be in APPROVED status
    - Wallet must have sufficient balance
    """
    loan = LoanRequest.query.get(loan_id)
    if not loan:
        return False, "Loan not found"

    if not loan.is_active:
        return False, "This loan request is no longer active"

    if loan.status != LoanStatus.APPROVED.value:
        return False, f"Cannot disburse. Loan status is {loan.status}"

    if not is_group_admin(user_id, loan.group_id):
        return False, "Only group admin can disburse loans"

    # Check wallet balance
    group = Group.query.get(loan.group_id)
    if not group.wallet:
        return False, "Group wallet not found"

    disburse_amount = loan.approved_amount or loan.amount
    if group.wallet.balance < disburse_amount:
        return False, f"Insufficient balance. Required: ₹{disburse_amount}, Available: ₹{group.wallet.balance}"

    return True, None


# ============================================================
# LOAN REPAYMENT AUTHORIZATION
# ============================================================

def can_repay(user_id, loan_id):
    """
    Check if user can submit repayment.

    Requirements:
    - Loan must be DISBURSED
    - User must be the borrower
    - Loan must not be fully repaid
    """
    loan = LoanRequest.query.get(loan_id)
    if not loan:
        return False, "Loan not found"

    if loan.status != LoanStatus.DISBURSED.value:
        if loan.status == LoanStatus.COMPLETED.value:
            return False, "This loan is already fully repaid"
        return False, f"Cannot repay. Loan status is {loan.status}"

    if loan.requested_by != user_id:
        return False, "Only the borrower can repay this loan"

    if loan.is_fully_repaid():
        return False, "This loan is already fully repaid"

    return True, None


def can_approve_repayment(user_id, repayment_id):
    """
    Check if user can approve repayment.

    Requirements:
    - User must be group admin
    - Repayment must be in PENDING status
    """
    repayment = LoanRepayment.query.get(repayment_id)
    if not repayment:
        return False, "Repayment not found"

    if repayment.status != RepaymentStatus.PENDING.value:
        return False, f"Repayment is already {repayment.status}"

    loan = LoanRequest.query.get(repayment.loan_id)
    if not is_group_admin(user_id, loan.group_id):
        return False, "Only group admin can approve repayments"

    return True, None


# ============================================================
# GROUP LEAVE AUTHORIZATION (UPDATED)
# ============================================================

def can_leave_group(user_id, group_id):
    """
    Check if user can leave group.

    Requirements:
    - User must be active member
    - User must NOT have active/unpaid loans
    - If admin: must transfer admin rights first
    - No pending withdrawal requests
    """
    membership = get_membership(user_id, group_id)
    if not membership:
        return False, "You are not a member of this group"

    # Check for pending withdrawal requests
    pending_withdrawals = WithdrawalRequest.query.filter_by(
        user_id=user_id,
        group_id=group_id,
        status=WithdrawalStatus.PENDING.value
    ).count()

    if pending_withdrawals > 0:
        return False, "You have pending withdrawal requests"

    # Check for active loans
    active_loans = LoanRequest.query.filter(
        LoanRequest.group_id == group_id,
        LoanRequest.requested_by == user_id,
        LoanRequest.is_active == True,
        LoanRequest.status.in_([
            LoanStatus.PENDING.value,
            LoanStatus.APPROVED.value,
            LoanStatus.DISBURSED.value
        ])
    ).all()

    if active_loans:
        # Check each loan
        for loan in active_loans:
            if loan.status == LoanStatus.PENDING.value:
                return False, "You have pending loan requests. Cancel them first."
            elif loan.status == LoanStatus.APPROVED.value:
                return False, "You have an approved loan pending disbursement."
            elif loan.status == LoanStatus.DISBURSED.value:
                remaining = loan.get_remaining_amount()
                return False, f"You have an unpaid loan of ₹{remaining:.2f}. Clear it first."

    # Check pending repayments
    pending_repayments = LoanRepayment.query.join(LoanRequest).filter(
        LoanRequest.group_id == group_id,
        LoanRepayment.paid_by == user_id,
        LoanRepayment.status == RepaymentStatus.PENDING.value
    ).count()

    if pending_repayments > 0:
        return False, f"You have pending repayment(s) awaiting approval."

    # Check if admin
    if membership.role == MemberRole.ADMIN.value:
        # Count other admins
        admin_count = GroupMember.query.filter_by(
            group_id=group_id,
            role=MemberRole.ADMIN.value,
            is_active=True
        ).count()

        if admin_count == 1:
            return False, "You are the only admin. Transfer admin rights first."

    return True, None


# ============================================================
# ADMIN TRANSFER AUTHORIZATION
# ============================================================

def can_transfer_admin(from_user_id, to_user_id, group_id):
    """
    Check if admin transfer is allowed.

    Requirements:
    - From user must be current admin
    - To user must be active member
    - To user must not already be admin
    """
    from_membership = get_membership(from_user_id, group_id)
    if not from_membership:
        return False, "You are not a member of this group"

    if from_membership.role != MemberRole.ADMIN.value:
        return False, "You are not an admin of this group"

    to_membership = get_membership(to_user_id, group_id)
    if not to_membership:
        return False, "Target user is not a member of this group"

    if to_membership.role == MemberRole.ADMIN.value:
        return False, "Target user is already an admin"

    return True, None


# ============================================================
# REJOIN GROUP AUTHORIZATION (NEW)
# ============================================================

def can_rejoin_group(user_id, group_id):
    """
    Check if user can rejoin group.

    Requirements:
    - User must NOT be currently active member
    - If previous membership existed, must have no outstanding liabilities
    - Group must be active
    """
    # Check if already active member
    active_membership = get_membership(user_id, group_id)
    if active_membership:
        return False, "You are already an active member of this group"

    # Check if group is active
    group = Group.query.get(group_id)
    if not group or not group.is_active:
        return False, "Group is not active or doesn't exist"

    # Check for inactive membership
    inactive_membership = GroupMember.query.filter_by(
        group_id=group_id,
        user_id=user_id,
        is_active=False
    ).first()

    if not inactive_membership:
        return True, "No previous membership found (can join as new member)"

    # Check if there were any liabilities when they left
    # For now, we'll allow rejoining
    # You could add checks for old loans, etc.

    return True, "Can rejoin group"

# ============================================================
# HELPER FUNCTION: REQUIRE AUTHORIZATION
# ============================================================

def require_authorization(check_func, *args, error_class=AuthorizationError):
    """
    Wrapper to raise exception if authorization fails.

    Usage:
        require_authorization(can_vote, user_id, loan_id)
    """
    allowed, reason = check_func(*args)
    if not allowed:
        raise error_class(reason)
    return True