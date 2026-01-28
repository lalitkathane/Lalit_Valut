from app.models import *
from app.services.helperfunctions import *

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
    wallet = GroupWallet.query.get(wallet_id)
    if not wallet:
        return False, "Wallet not found"
    if not is_group_member(user_id, wallet.group_id):
        return False, "You are not a member of this group"
    return True, None


# ============================================================
# WITHDRAWAL AUTHORIZATION
# ============================================================

def can_withdraw(user_id, group_id):
    # Check membership
    membership = get_membership(user_id, group_id)
    if not membership:
        return False, "You are not an active member of this group"

    # Check for pending/active loans using helper
    active_loans = get_active_loans(user_id, group_id)

    # Use new helper to get loan status message
    loan_msg = check_loans_and_get_message(active_loans, "You", "before withdrawing")
    if loan_msg:
        return False, loan_msg

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

    # Check for pending withdrawal requests using helper
    if has_pending_withdrawals(user_id, group_id):
        return False, "You already have a pending withdrawal request"

    return True, None


def can_approve_withdrawal(user_id, withdrawal_id):
    withdrawal = WithdrawalRequest.query.get(withdrawal_id)
    if not withdrawal:
        return False, "Withdrawal request not found"

    if withdrawal.status != WithdrawalStatus.PENDING.value:
        return False, f"Withdrawal is already {withdrawal.status}"

    if not is_group_admin(user_id, withdrawal.group_id):
        return False, "Only group admin can approve withdrawals"

    # Check if member has pending/active loans using helper
    active_loans = get_active_loans(withdrawal.user_id, withdrawal.group_id)

    # Use new helper for loan status message
    loan_msg = check_loans_and_get_message(active_loans, "Member", "Cannot approve withdrawal")
    if loan_msg:
        return False, loan_msg

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
# GROUP LEAVE AUTHORIZATION
# ============================================================

def can_leave_group(user_id, group_id):
    membership = get_membership(user_id, group_id)
    if not membership:
        return False, "You are not a member of this group"

    # Check if this is the only active member AND wallet balance is zero
    # Using helper for member count
    active_members_count = get_active_members_count(group_id)

    wallet = GroupWallet.query.filter_by(group_id=group_id).first()
    wallet_balance = wallet.balance if wallet else 0

    # Special case: if this is the only member AND wallet balance is zero
    # Skip most checks since group will be deleted
    if active_members_count == 1 and wallet_balance == 0:
        # Still check for pending withdrawal requests using helper
        if has_pending_withdrawals(user_id, group_id):
            return False, "You have pending withdrawal requests. Please resolve them first."

        return True, "Last member can leave (group will be deleted)"

    # NORMAL CHECKS (for non-last members or when wallet has balance)

    # Check for pending withdrawal requests using helper
    if has_pending_withdrawals(user_id, group_id):
        return False, "You have pending withdrawal requests"

    # Check for active loans using helper
    active_loans = get_active_loans(user_id, group_id)

    # Use new helper for loan status message
    loan_msg = check_loans_and_get_message(active_loans, "You", "Clear it first")
    if loan_msg:
        return False, loan_msg

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
        # Count other admins using helper
        admin_count = get_admin_count(group_id)

        if admin_count == 1:
            # But check if this is the only member overall
            if active_members_count == 1:
                # Special case: only admin and only member
                # Check wallet balance - if zero, allow leaving (handled by special case above)
                # If not zero, show appropriate message
                if wallet_balance > 0:
                    return False, "You are the only admin and member. Withdraw your contributions first or delete the group."
                # If wallet_balance == 0, this would have been caught by the special case above
            else:
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
# REJOIN GROUP AUTHORIZATION
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