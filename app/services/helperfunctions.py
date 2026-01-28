from app.models import *
from datetime import datetime
from sqlalchemy import func, case


# ====================================
#  LOAN-RELATED HELPERS
# ====================================

def get_active_loans(user_id, group_id):
    """Get all active loans (pending/approved/disbursed) for a user in a group"""
    return LoanRequest.query.filter(
        LoanRequest.group_id == group_id,
        LoanRequest.requested_by == user_id,
        LoanRequest.is_active == True,
        LoanRequest.status.in_([
            LoanStatus.PENDING.value,
            LoanStatus.APPROVED.value,
            LoanStatus.DISBURSED.value
        ])
    ).all()


def check_loans_and_get_message(active_loans, user_type="You", context_action=None):
    """
    Check active loans and return appropriate error message

    Args:
        active_loans: List of active loans from get_active_loans()
        user_type: "You" for self-checks, "Member" for admin checking others
        context_action: Additional context like "withdrawing", "leaving", etc.

    Returns:
        Error message string or None if no issues
    """
    if not active_loans:
        return None

    for loan in active_loans:
        if loan.status == LoanStatus.PENDING.value:
            action_suffix = f" {context_action}" if context_action else ""
            return f"{user_type} have a pending loan request.{action_suffix}"
        elif loan.status == LoanStatus.APPROVED.value:
            action_suffix = f" {context_action}" if context_action else ""
            return f"{user_type} have an approved loan pending disbursement.{action_suffix}"
        elif loan.status == LoanStatus.DISBURSED.value:
            remaining = loan.get_remaining_amount()
            action_suffix = f" {context_action}" if context_action else ""
            return f"{user_type} have an active loan with ₹{remaining:.2f} remaining.{action_suffix}"

    return None


def get_voting_stats(loan_id):
    """Get approval and rejection counts for a loan"""
    approvals = LoanApproval.query.filter_by(
        loan_id=loan_id,
        approved=True
    ).count()

    rejections = LoanApproval.query.filter_by(
        loan_id=loan_id,
        approved=False
    ).count()

    return {
        'approvals': approvals,
        'rejections': rejections,
        'votes_cast': approvals + rejections
    }


def get_emi_schedule(loan_id):
    """Get EMI schedule for a loan"""
    return EMISchedule.query.filter_by(
        loan_id=loan_id
    ).order_by(
        EMISchedule.installment_number
    ).all()


def get_paid_emis_count(loan_id):
    """Get count of paid EMIs for a loan"""
    return EMISchedule.query.filter_by(
        loan_id=loan_id,
        is_paid=True
    ).count()


def get_user_loans(user_id, is_active=True):
    """Get all loans for a user (as borrower)"""
    query = LoanRequest.query.filter_by(requested_by=user_id)
    if is_active is not None:
        query = query.filter_by(is_active=is_active)
    return query.all()


def get_pending_loans(user_id, group_id):
    """Get pending loan requests for a user in a group"""
    return LoanRequest.query.filter_by(
        group_id=group_id,
        requested_by=user_id,
        status=LoanStatus.PENDING.value,
        is_active=True
    ).all()


def get_active_loans_for_user_group(user_id, group_id):
    """Get active loans (approved/disbursed) for a user in a group"""
    return LoanRequest.query.filter(
        LoanRequest.group_id == group_id,
        LoanRequest.requested_by == user_id,
        LoanRequest.is_active == True,
        LoanRequest.status.in_([LoanStatus.APPROVED.value, LoanStatus.DISBURSED.value])
    ).all()


# ====================================
#  MEMBERSHIP-RELATED HELPERS
# ====================================

def get_active_members_count(group_id):
    """Get count of active members in a group"""
    return GroupMember.query.filter_by(
        group_id=group_id,
        is_active=True
    ).count()


def get_admin_count(group_id):
    """Get count of active admins in a group"""
    return GroupMember.query.filter_by(
        group_id=group_id,
        role=MemberRole.ADMIN.value,
        is_active=True
    ).count()


def get_admin_members(group_id):
    """Get all active admin members in a group"""
    return GroupMember.query.filter_by(
        group_id=group_id,
        role=MemberRole.ADMIN.value,
        is_active=True
    ).all()


def get_user_memberships(user_id, is_active=True):
    """Get all memberships for a user"""
    query = GroupMember.query.filter_by(user_id=user_id)
    if is_active is not None:
        query = query.filter_by(is_active=is_active)
    return query.all()


def get_membership(user_id, group_id):
    """Get active membership record"""
    return GroupMember.query.filter_by(
        user_id=user_id,
        group_id=group_id,
        is_active=True
    ).first()


def get_membership_including_inactive(user_id, group_id):
    """Get membership record (active or inactive)"""
    return GroupMember.query.filter_by(
        user_id=user_id,
        group_id=group_id
    ).first()


# ====================================
#  WITHDRAWAL-RELATED HELPERS
# ====================================

def has_pending_withdrawals(user_id, group_id):
    """Check if user has pending withdrawal requests"""
    return WithdrawalRequest.query.filter_by(
        user_id=user_id,
        group_id=group_id,
        status=WithdrawalStatus.PENDING.value
    ).count() > 0


def get_pending_withdrawals(user_id, group_id):
    """Get all pending withdrawal requests for a user in a group"""
    return WithdrawalRequest.query.filter_by(
        user_id=user_id,
        group_id=group_id,
        status=WithdrawalStatus.PENDING.value
    ).all()


def get_pending_withdrawals_count(user_id, group_id):
    """Get count of pending withdrawal requests for a user"""
    return WithdrawalRequest.query.filter_by(
        user_id=user_id,
        group_id=group_id,
        status=WithdrawalStatus.PENDING.value
    ).count()


def get_user_withdrawals(user_id, group_id=None):
    """Get all withdrawal requests for a user (optionally filtered by group)"""
    query = WithdrawalRequest.query.filter_by(user_id=user_id)
    if group_id:
        query = query.filter_by(group_id=group_id)
    return query.order_by(WithdrawalRequest.created_at.desc()).all()


# ====================================
#  REPAYMENT-RELATED HELPERS
# ====================================

def get_pending_repayments(user_id, group_id):
    """Get pending repayments for a user in a group"""
    return LoanRepayment.query.join(LoanRequest).filter(
        LoanRequest.group_id == group_id,
        LoanRepayment.paid_by == user_id,
        LoanRepayment.status == RepaymentStatus.PENDING.value
    ).all()


# ====================================
#  FINANCIAL HELPERS
# ====================================

def get_group_wallet(group_id):
    """Get wallet for a group"""
    return GroupWallet.query.filter_by(group_id=group_id).first()


def get_user_ledgers(user_id):
    """Get all ledgers for a user"""
    return MemberLedger.query.filter_by(user_id=user_id).all()


def get_user_ledger_for_group(user_id, group_id, active_only=True):
    """Get ledger for a user in a specific group"""
    wallet = get_group_wallet(group_id)
    if not wallet:
        return None

    query = MemberLedger.query.filter_by(
        wallet_id=wallet.id,
        user_id=user_id
    )

    if active_only:
        query = query.filter_by(is_active=True)

    return query.first()


def get_user_contributions(user_id, wallet_id=None):
    """Get contributions for a user (optionally filtered by wallet)"""
    query = MemberContribution.query.filter_by(user_id=user_id)
    if wallet_id:
        query = query.filter_by(wallet_id=wallet_id)
    return query.order_by(MemberContribution.contributed_at.desc()).all()


def get_interest_distributions_to_user(user_id, group_id=None):
    """Get interest distributions to a user (optionally filtered by group)"""
    query = InterestDistribution.query.filter_by(beneficiary_id=user_id)
    if group_id:
        # Filter by group using LoanRequest join
        query = query.join(LoanRequest).filter(LoanRequest.group_id == group_id)
    return query.order_by(InterestDistribution.created_at.desc()).all()


def get_user_loans_for_group(user_id, group_id):
    """Get loans for a user in a specific group"""
    return LoanRequest.query.filter_by(
        group_id=group_id,
        requested_by=user_id,
        is_active=True
    ).order_by(LoanRequest.created_at.desc()).all()


# ====================================
#  WALLET-RELATED HELPERS (NEW)
# ====================================

def get_member_ledger_for_wallet(wallet_id, user_id):
    """Get member ledger for a wallet"""
    return MemberLedger.query.filter_by(
        wallet_id=wallet_id,
        user_id=user_id
    ).first()


def get_wallet_transactions(wallet_id, limit=None):
    """Get transactions for a wallet"""
    query = WalletTransaction.query.filter_by(
        wallet_id=wallet_id,
        is_reversed=False
    ).order_by(WalletTransaction.created_at.desc())

    if limit:
        query = query.limit(limit)

    return query.all()


def get_wallet_transaction_totals(wallet_id):
    """Get transaction type totals for a wallet"""
    return WalletTransaction.query.filter_by(
        wallet_id=wallet_id,
        is_reversed=False
    ).with_entities(
        func.sum(case((WalletTransaction.transaction_type == TransactionType.CONTRIBUTION.value,
                       WalletTransaction.amount), else_=0)).label('total_contributions'),
        func.sum(case((WalletTransaction.transaction_type == TransactionType.LOAN_DISBURSEMENT.value,
                       WalletTransaction.amount), else_=0)).label('total_disbursements'),
        func.sum(case((WalletTransaction.transaction_type == TransactionType.REPAYMENT.value,
                       WalletTransaction.amount), else_=0)).label('total_repayments'),
        func.sum(case((WalletTransaction.transaction_type == TransactionType.WITHDRAWAL.value,
                       WalletTransaction.amount), else_=0)).label('total_withdrawals')
    ).first()


def get_transaction_counts_by_type(wallet_id):
    """Get transaction counts by type"""
    return {
        'contributions': WalletTransaction.query.filter_by(
            wallet_id=wallet_id,
            transaction_type=TransactionType.CONTRIBUTION.value,
            is_reversed=False
        ).count(),
        'disbursements': WalletTransaction.query.filter_by(
            wallet_id=wallet_id,
            transaction_type=TransactionType.LOAN_DISBURSEMENT.value,
            is_reversed=False
        ).count(),
        'repayments': WalletTransaction.query.filter_by(
            wallet_id=wallet_id,
            transaction_type=TransactionType.REPAYMENT.value,
            is_reversed=False
        ).count(),
        'withdrawals': WalletTransaction.query.filter_by(
            wallet_id=wallet_id,
            transaction_type=TransactionType.WITHDRAWAL.value,
            is_reversed=False
        ).count()
    }


def get_contribution_snapshots_for_loan(loan_id):
    """Get contribution snapshots for a loan"""
    from app.models import LoanContributionSnapshot
    return LoanContributionSnapshot.query.filter_by(loan_id=loan_id).all()


def get_pending_disbursements_for_group(group_id):
    """Get approved loans pending disbursement for a group"""
    return LoanRequest.query.filter_by(
        group_id=group_id,
        status=LoanStatus.APPROVED.value,
        is_active=True
    ).filter(LoanRequest.disbursed_at.is_(None)).all()


def get_disbursed_loans_for_group(group_id):
    """Get disbursed loans for a group"""
    return LoanRequest.query.filter(
        LoanRequest.group_id == group_id,
        LoanRequest.status == LoanStatus.DISBURSED.value,
        LoanRequest.is_active == True
    ).all()


# ===============================================================================================
#                                 Routes Helpers
# ===============================================================================================
# =======================================
#  ADMIN-RELATED HELPERS (NEW)
# ========================================

def get_pending_loans_for_group(group_id):
    """Get pending loan approvals for a group"""
    from app.models import LoanRequest, LoanStatus
    return LoanRequest.query.filter_by(
        group_id=group_id,
        status=LoanStatus.PENDING.value,
        is_active=True
    ).all()


def get_approved_loans_pending_disbursement(group_id):
    """Get approved loans awaiting disbursement for a group"""
    from app.models import LoanRequest, LoanStatus
    return LoanRequest.query.filter_by(
        group_id=group_id,
        status=LoanStatus.APPROVED.value,
        is_active=True
    ).filter(LoanRequest.disbursed_at.is_(None)).all()


def get_pending_repayments_for_group(group_id):
    """Get all pending repayments for a group"""
    from app.models import LoanRepayment, LoanRequest, RepaymentStatus
    return LoanRepayment.query.join(LoanRequest).filter(
        LoanRequest.group_id == group_id,
        LoanRepayment.status == RepaymentStatus.PENDING.value
    ).order_by(LoanRepayment.submitted_at.asc()).all()


def get_active_loans_for_group(group_id):
    """Get active disbursed loans for a group"""
    from app.models import LoanRequest, LoanStatus
    return LoanRequest.query.filter_by(
        group_id=group_id,
        status=LoanStatus.DISBURSED.value,
        is_active=True
    ).all()


def get_pending_withdrawals_for_group(group_id):
    """Get pending withdrawal requests for a group"""
    from app.models import WithdrawalRequest, WithdrawalStatus
    return WithdrawalRequest.query.filter_by(
        group_id=group_id,
        status=WithdrawalStatus.PENDING.value
    ).order_by(WithdrawalRequest.created_at.desc()).all()


def get_admin_transfer_history(group_id):
    """Get admin transfer history for a group"""
    from app.models import AdminTransferHistory
    return AdminTransferHistory.query.filter_by(
        group_id=group_id
    ).order_by(AdminTransferHistory.transferred_at.desc()).all()


# ====================================
#  COMMON ROUTE HELPERS (NEW)
# ====================================

def require_group_admin(user_id, group_id, flash_message="Admin access required!"):
    """
    Common helper to check if user is group admin

    Returns:
        Tuple: (is_admin, flash_message) - if not admin, flash_message is provided
    """
    from app.services.authorization_service import is_group_admin

    if not is_group_admin(user_id, group_id):
        return False, flash_message
    return True, None


def get_group_or_404(group_id):
    """Get group or return 404"""
    from app.models import Group
    return Group.query.get_or_404(group_id)


def get_member_ledger_for_group(user_id, group_id):
    """Get member ledger for a user in a specific group"""
    from app.models import Group, MemberLedger

    group = Group.query.get_or_404(group_id)
    if not group.wallet:
        return None

    return MemberLedger.query.filter_by(
        wallet_id=group.wallet.id,
        user_id=user_id
    ).first()


# ====================================
#  AUTH-RELATED HELPERS (NEW)
# ====================================

def validate_user_registration_data(name, email, phone, password, confirm_password):
    """Validate user registration data and return error message if any"""
    import re

    if not name or not email or not phone or not password:
        return 'All fields are required!'

    if password != confirm_password:
        return 'Passwords do not match!'

    if len(password) < 6:
        return 'Password must be at least 6 characters!'

    # Validate phone format (Indian mobile numbers)
    pattern = r'^[6-9]\d{9}$'
    if not bool(re.match(pattern, phone)):
        return 'Please enter a valid 10-digit mobile number!'

    # Basic email validation
    if '@' not in email or '.' not in email:
        return 'Please enter a valid email address!'

    return None


def user_exists_by_email_or_phone(email, phone):
    """Check if user already exists by email or phone"""
    from app.models import User
    existing_user = User.query.filter((User.email == email) | (User.phone == phone)).first()
    return existing_user


def get_user_by_identity(identity):
    """Get user by email or phone"""
    from app.models import User
    return User.query.filter((User.email == identity) | (User.phone == identity)).first()


# ====================================
#  DASHBOARD-RELATED HELPERS (NEW)
# ====================================

def get_active_user_loans(user_id):
    """Get active loans for a user (disbursed and not fully repaid)"""
    from app.models import LoanRequest, LoanStatus
    return LoanRequest.query.filter_by(
        requested_by=user_id,
        is_active=True
    ).filter(
        LoanRequest.status == LoanStatus.DISBURSED.value,
        LoanRequest.total_repaid < LoanRequest.total_repayable  # Not fully repaid
    ).all()


def get_next_emi_for_user(user_id):
    """Get next EMI due for a user"""
    from app.models import EMISchedule, LoanRequest, LoanStatus
    from datetime import datetime

    return EMISchedule.query.join(LoanRequest).filter(
        LoanRequest.requested_by == user_id,
        LoanRequest.is_active == True,
        LoanRequest.status == LoanStatus.DISBURSED.value,
        LoanRequest.total_repaid < LoanRequest.total_repayable,  # Not fully repaid
        EMISchedule.is_paid == False,
        EMISchedule.due_date >= datetime.utcnow().date()
    ).order_by(EMISchedule.due_date).first()


def get_pending_votes_for_user(user_id):
    """Get pending votes for a user across all memberships"""
    from app.models import GroupMember, LoanRequest, LoanStatus, LoanApproval

    pending_votes = 0
    pending_loan_for_vote = None

    memberships = GroupMember.query.filter_by(
        user_id=user_id,
        is_active=True
    ).all()

    for membership in memberships:
        group_loans = LoanRequest.query.filter_by(
            group_id=membership.group_id,
            status=LoanStatus.PENDING.value,
            is_active=True
        ).all()

        for loan in group_loans:
            if loan.requested_by != user_id:  # Don't vote on own loans
                existing_vote = LoanApproval.query.filter_by(
                    loan_id=loan.id,
                    user_id=user_id
                ).first()
                if not existing_vote:
                    pending_votes += 1
                    if not pending_loan_for_vote:
                        pending_loan_for_vote = loan

    return pending_votes, pending_loan_for_vote


def get_pending_repayment_approvals_for_user(user_id):
    """Get pending repayment approvals for admin user"""
    from app.models import GroupMember, LoanRepayment, LoanRequest, RepaymentStatus, MemberRole

    pending_repayment_approvals = 0
    pending_repayment_loan = None

    memberships = GroupMember.query.filter_by(
        user_id=user_id,
        is_active=True,
        role=MemberRole.ADMIN.value
    ).all()

    for membership in memberships:
        count = LoanRepayment.query.join(LoanRequest).filter(
            LoanRequest.group_id == membership.group_id,
            LoanRepayment.status == RepaymentStatus.PENDING.value
        ).count()
        pending_repayment_approvals += count

        if count > 0 and not pending_repayment_loan:
            loan_with_pending = LoanRequest.query.join(LoanRepayment).filter(
                LoanRequest.group_id == membership.group_id,
                LoanRepayment.status == RepaymentStatus.PENDING.value
            ).first()
            if loan_with_pending:
                pending_repayment_loan = loan_with_pending

    return pending_repayment_approvals, pending_repayment_loan


def get_pending_withdrawal_approvals_for_user(user_id):
    """Get pending withdrawal approvals for admin user"""
    from app.models import GroupMember, WithdrawalRequest, WithdrawalStatus, MemberRole

    pending_withdrawal_approvals = 0
    pending_withdrawal_request = None

    memberships = GroupMember.query.filter_by(
        user_id=user_id,
        is_active=True,
        role=MemberRole.ADMIN.value
    ).all()

    for membership in memberships:
        withdrawal_count = WithdrawalRequest.query.filter_by(
            group_id=membership.group_id,
            status=WithdrawalStatus.PENDING.value
        ).count()
        pending_withdrawal_approvals += withdrawal_count

        if withdrawal_count > 0 and not pending_withdrawal_request:
            pending_withdrawal_request = WithdrawalRequest.query.filter_by(
                group_id=membership.group_id,
                status=WithdrawalStatus.PENDING.value
            ).first()

    return pending_withdrawal_approvals, pending_withdrawal_request


def get_user_active_groups(user_id):
    """Get active groups for a user"""
    from app.models import GroupMember, Group

    memberships = GroupMember.query.filter_by(
        user_id=user_id,
        is_active=True
    ).all()

    groups = []
    for membership in memberships:
        group = Group.query.get(membership.group_id)
        if group:
            groups.append(group)

    return groups


def calculate_total_outstanding(active_loans):
    """Calculate total outstanding amount from active loans"""
    return sum(loan.get_remaining_amount() for loan in active_loans)


# ====================================
#  GROUP-RELATED HELPERS (NEW)
# ====================================

def get_active_members(group_id):
    """Get all active members of a group"""
    return GroupMember.query.filter_by(
        group_id=group_id,
        is_active=True
    ).all()


def get_pending_loans_for_group_sorted(group_id):
    """Get pending loan requests for a group, sorted by creation date"""
    from app.models import LoanRequest, LoanStatus
    return LoanRequest.query.filter_by(
        group_id=group_id,
        status=LoanStatus.PENDING.value,
        is_active=True
    ).order_by(LoanRequest.created_at.desc()).all()


def get_eligible_members_for_admin_transfer(group_id, exclude_user_id):
    """Get members eligible for admin transfer (non-admins)"""
    from app.models import GroupMember, MemberRole
    return GroupMember.query.filter(
        GroupMember.group_id == group_id,
        GroupMember.is_active == True,
        GroupMember.role != MemberRole.ADMIN.value,
        GroupMember.user_id != exclude_user_id
    ).all()


def get_member_profile_data(user_id, group_id):
    """Get comprehensive data for a member profile page"""
    from app.models import User, GroupMember, MemberLedger, LoanRequest, WithdrawalRequest, Group

    group = Group.query.get_or_404(group_id)
    member = User.query.get_or_404(user_id)

    membership = GroupMember.query.filter_by(
        group_id=group_id,
        user_id=user_id,
        is_active=True
    ).first()

    if not membership:
        return None

    # Get member ledger
    ledger = None
    if group.wallet:
        ledger = MemberLedger.query.filter_by(
            wallet_id=group.wallet.id,
            user_id=user_id
        ).first()

    # Get loans
    loans = LoanRequest.query.filter_by(
        group_id=group_id,
        requested_by=user_id,
        is_active=True
    ).all()

    # Get withdrawal history
    withdrawals = WithdrawalRequest.query.filter_by(
        group_id=group_id,
        user_id=user_id
    ).order_by(WithdrawalRequest.created_at.desc()).limit(10).all()

    return {
        'group': group,
        'member': member,
        'membership': membership,
        'ledger': ledger,
        'loans': loans,
        'withdrawals': withdrawals
    }


def has_active_loans_in_group(group_id):
    """Check if there are any active loans in a group"""
    from app.models import LoanRequest, LoanStatus

    return LoanRequest.query.filter_by(
        group_id=group_id,
        is_active=True
    ).filter(
        LoanRequest.status.in_([
            LoanStatus.PENDING.value,
            LoanStatus.PRE_APPROVED.value,
            LoanStatus.APPROVED.value,
            LoanStatus.DISBURSED.value
        ])
    ).count() > 0


def get_admin_count_in_group(group_id):
    """Get count of active admins in a group"""
    from app.models import GroupMember, MemberRole
    return GroupMember.query.filter_by(
        group_id=group_id,
        role=MemberRole.ADMIN.value,
        is_active=True
    ).count()


def can_user_leave_group(user_id, group_id):
    """Check if a user can leave a group"""
    from app.services.authorization_service import can_leave_group
    return can_leave_group(user_id, group_id)


def validate_group_creation_data(name, description, interest_rate, loan_duration, repayment_type):
    """Validate group creation form data"""
    if not name or len(name.strip()) == 0:
        return False, 'Group name is required!'

    if interest_rate <= 0:
        return False, 'Interest rate must be positive!'

    if loan_duration <= 0:
        return False, 'Loan duration must be positive!'

    if repayment_type not in ['emi', 'bulk']:
        return False, 'Invalid repayment type!'

    return True, None


def get_group_awaiting_disbursement(group_id):
    """Get approved loans awaiting disbursement for a group"""
    from app.models import LoanRequest, LoanStatus
    return LoanRequest.query.filter_by(
        group_id=group_id,
        status=LoanStatus.APPROVED.value,
        is_active=True
    ).filter(LoanRequest.disbursed_at.is_(None)).all()


def get_group_pending_repayments(group_id):
    """Get pending repayments for a group"""
    from app.models import LoanRepayment, LoanRequest, RepaymentStatus
    return LoanRepayment.query.join(LoanRequest).filter(
        LoanRequest.group_id == group_id,
        LoanRepayment.status == RepaymentStatus.PENDING.value
    ).all()


def is_last_admin_leaving(user_id, group_id):
    """Check if the leaving user is the last admin"""
    is_admin = is_group_admin(user_id, group_id)
    if not is_admin:
        return False

    admin_count = get_admin_count_in_group(group_id)
    return admin_count <= 1


def is_group_empty(group_id):
    """Check if group has only one active member (the user checking)"""
    active_members_count = GroupMember.query.filter_by(
        group_id=group_id,
        is_active=True
    ).count()

    return active_members_count == 1


def is_group_wallet_empty(group_id):
    """Check if group wallet has zero balance"""
    from app.models import Group, GroupWallet

    group = Group.query.get(group_id)
    if not group or not group.wallet:
        return True

    return group.wallet.balance == 0


# ====================================
#  LOAN ROUTE HELPERS (NEW)
# ====================================

def validate_loan_creation_data(amount, reason, group_min_emi_duration=None, loan_duration=None):
    """Validate loan creation form data"""
    if amount <= 0:
        return False, 'Please enter a valid amount greater than zero!'

    if amount != int(amount):
        return False, 'Please enter a whole number (no decimals allowed)!'

    if not reason or len(reason.strip()) == 0:
        return False, 'Please provide a reason for the loan request!'

    # Validate minimum EMI duration if provided
    if group_min_emi_duration is not None and loan_duration is not None:
        if loan_duration < group_min_emi_duration:
            return False, f'Loan duration must be at least {group_min_emi_duration} months (group policy)!'

    return True, None


def get_loan_view_data(loan_id, user_id):
    """Get comprehensive data for loan detail view"""
    from app.models import LoanRequest, Group, LoanApproval, LoanRepayment, RepaymentStatus, EMISchedule
    from datetime import datetime
    import app.extensions as db

    loan = LoanRequest.query.get_or_404(loan_id)
    group = loan.group

    # Get loan details from service
    from app.services.loan_service import get_loan_details
    details = get_loan_details(loan_id)

    # Get user vote
    user_vote = LoanApproval.query.filter_by(
        loan_id=loan_id,
        user_id=user_id
    ).first()

    # Get all votes
    all_votes = LoanApproval.query.filter_by(loan_id=loan_id).all()

    # Check authorization
    from app.services.authorization_service import can_vote, can_repay, is_group_admin
    can_vote_result, vote_reason = can_vote(user_id, loan_id)
    can_repay_result, _ = can_repay(user_id, loan_id)
    is_admin = is_group_admin(user_id, loan.group_id)

    # Check if admin can perform final approval
    can_final_approve = (
            is_admin and
            loan.status == LoanStatus.PRE_APPROVED.value and
            loan.requested_by != user_id
    )

    # Get pending repayments for admin
    pending_repayments = []
    if is_admin:
        pending_repayments = LoanRepayment.query.filter_by(
            loan_id=loan_id,
            status=RepaymentStatus.PENDING.value
        ).all()

    today = datetime.utcnow().date()

    # Check if borrower has ANY repayment this month
    has_repayment_this_month = False
    if not is_admin and user_id == loan.requested_by:
        current_month = datetime.utcnow().month
        current_year = datetime.utcnow().year

        repayment_this_month = LoanRepayment.query.filter(
            LoanRepayment.loan_id == loan_id,
            LoanRepayment.paid_by == user_id,
            db.extract('month', LoanRepayment.submitted_at) == current_month,
            db.extract('year', LoanRepayment.submitted_at) == current_year
        ).first()

        has_repayment_this_month = repayment_this_month is not None

    # Calculate vote progress
    vote_progress = 0
    if loan.status == LoanStatus.PENDING.value and details.get('voting'):
        voting_dict = details.get('voting', {})
        required_approvals = voting_dict.get('required_approvals', 1)
        approvals = voting_dict.get('approvals', 0)
        vote_progress = (approvals / required_approvals * 100) if required_approvals > 0 else 0

    # Calculate repayment progress
    repay_progress = 0
    if loan.total_repayable and loan.total_repayable > 0:
        repay_progress = (loan.total_repaid / loan.total_repayable * 100) if loan.total_repaid else 0
        repay_progress = min(100, repay_progress)

    # Calculate days until next EMI
    days_left = 0
    if loan.status == LoanStatus.DISBURSED.value and details.get('next_emi'):
        next_emi = details['next_emi']
        if isinstance(next_emi, dict):
            due_date = next_emi.get('due_date')
        else:
            due_date = getattr(next_emi, 'due_date', None)

        if due_date:
            days_left = (due_date - today).days
            days_left = max(0, days_left)

    return {
        'loan': loan,
        'group': group,
        'details': details,
        'user_vote': user_vote,
        'all_votes': all_votes,
        'can_vote': can_vote_result,
        'vote_reason': vote_reason,
        'can_repay': can_repay_result,
        'is_admin': is_admin,
        'can_final_approve': can_final_approve,
        'pending_repayments': pending_repayments,
        'today': today,
        'has_repayment_this_month': has_repayment_this_month,
        'vote_progress': vote_progress,
        'repay_progress': repay_progress,
        'days_left': days_left,
        'now': datetime.utcnow()
    }


def validate_repayment_amount(amount, loan, group, paid_emis):
    """Validate repayment amount with EMI restrictions"""
    from app.models import EMISchedule
    from datetime import datetime

    # Check basic validation
    if amount <= 0:
        return False, 'Please enter a valid amount greater than zero!'

    if not amount.is_integer():
        return False, 'Repayment amount must be a whole number (no decimals allowed)!'

    amount = int(amount)

    # Check EMI-specific validation
    if loan.loan_duration_months and loan.repayment_type == 'emi':
        remaining_amount = loan.get_remaining_amount()

        # Check if trying to pay more than next EMI
        if amount > loan.emi_amount:
            # Check if trying to pay full amount before minimum EMI duration
            if amount >= remaining_amount:
                can_make_full_payment = paid_emis >= group.min_emi_duration_months
                if not can_make_full_payment:
                    return False, f'You must complete at least {group.min_emi_duration_months} EMIs before making full payment. You have paid {paid_emis} EMIs so far.'
            else:
                return False, f'You can only pay one EMI at a time (₹{loan.emi_amount:,}). Minimum EMI duration is {group.min_emi_duration_months} months.'
        elif amount < loan.emi_amount:
            return False, f'Minimum payment is one EMI (₹{loan.emi_amount:,})'

    return True, None


def get_repayment_form_data(loan_id, user_id):
    """Get data for repayment form"""
    from app.models import LoanRequest, Group, EMISchedule
    from datetime import datetime

    loan = LoanRequest.query.get_or_404(loan_id)
    group = loan.group

    # Check if loan is fully repaid
    if loan.is_fully_repaid():
        return None, 'This loan has already been fully repaid!'

    # Check if user has already submitted ANY repayment for this month
    import app.extensions as db
    current_month = datetime.utcnow().month
    current_year = datetime.utcnow().year

    from app.models import LoanRepayment
    repayment_this_month = LoanRepayment.query.filter(
        LoanRepayment.loan_id == loan_id,
        LoanRepayment.paid_by == user_id,
        db.extract('month', LoanRepayment.submitted_at) == current_month,
        db.extract('year', LoanRepayment.submitted_at) == current_year
    ).first()

    if repayment_this_month:
        return None, 'You have already submitted a repayment for this month! You can only make one payment per month.'

    # Get EMI schedule if applicable
    emi_schedule = []
    next_emi = None
    if loan.repayment_type == 'emi':
        emi_schedule = EMISchedule.query.filter_by(loan_id=loan_id).order_by(
            EMISchedule.installment_number
        ).all()
        # Find next unpaid EMI
        next_emi = EMISchedule.query.filter_by(
            loan_id=loan_id,
            is_paid=False
        ).order_by(EMISchedule.installment_number).first()

    remaining_amount = loan.get_remaining_amount()

    # Calculate paid EMIs and remaining EMIs
    paid_emis = 0
    remaining_emis = 0
    can_make_full_payment = False

    if loan.loan_duration_months and loan.repayment_type == 'emi':
        paid_emis = EMISchedule.query.filter_by(
            loan_id=loan_id,
            is_paid=True
        ).count()

        remaining_emis = loan.loan_duration_months - paid_emis
        can_make_full_payment = paid_emis >= group.min_emi_duration_months

    return {
        'loan': loan,
        'group': group,
        'remaining_amount': remaining_amount,
        'emi_schedule': emi_schedule,
        'next_emi': next_emi,
        'paid_emis': paid_emis,
        'remaining_emis': remaining_emis,
        'can_make_full_payment': can_make_full_payment
    }, None


def get_my_loans_data(user_id):
    """Get data for my loans dashboard"""
    from app.models import LoanRequest, LoanApproval, LoanRepayment, LoanStatus, RepaymentStatus
    from datetime import datetime

    # Get all loans requested by user
    my_requests = LoanRequest.query.filter_by(
        requested_by=user_id,
        is_active=True
    ).order_by(LoanRequest.created_at.desc()).all()

    # Get pending votes (loans in user's groups that need voting)
    pending_votes = []
    from app.models import GroupMember
    memberships = GroupMember.query.filter_by(
        user_id=user_id,
        is_active=True
    ).all()

    for membership in memberships:
        group_loans = LoanRequest.query.filter_by(
            group_id=membership.group_id,
            status=LoanStatus.PENDING.value,
            is_active=True
        ).all()

        for loan in group_loans:
            if loan.requested_by == user_id:
                continue

            existing_vote = LoanApproval.query.filter_by(
                loan_id=loan.id,
                user_id=user_id
            ).first()

            if not existing_vote:
                pending_votes.append(loan)

    # Get my pending repayments
    my_pending_repayments = LoanRepayment.query.filter_by(
        paid_by=user_id,
        status=RepaymentStatus.PENDING.value
    ).all()

    return {
        'my_requests': my_requests,
        'pending_votes': pending_votes,
        'my_pending_repayments': my_pending_repayments,
        'today': datetime.utcnow().date()
    }


def get_emi_schedule_data(loan_id):
    """Get data for EMI schedule view"""
    from app.models import LoanRequest, EMISchedule

    loan = LoanRequest.query.get_or_404(loan_id)

    # Fetch all EMI records ordered by installment
    emi_schedule = EMISchedule.query.filter_by(loan_id=loan_id).order_by(
        EMISchedule.installment_number
    ).all()

    if not emi_schedule:
        return None, 'No EMI schedule found for this loan.'

    # Calculate totals from EMI records
    total_emi_sum = sum(e.emi_amount for e in emi_schedule)
    total_principal_sum = sum(e.principal_component for e in emi_schedule)
    total_interest_sum = sum(e.interest_component for e in emi_schedule)

    # Additional stats
    paid_installments = sum(1 for e in emi_schedule if e.is_paid)
    total_installments = len(emi_schedule)
    total_paid_amount = sum(e.paid_amount or e.emi_amount for e in emi_schedule if e.is_paid)

    return {
        'loan': loan,
        'emi_schedule': emi_schedule,
        'total_emi_sum': total_emi_sum,
        'total_principal_sum': total_principal_sum,
        'total_interest_sum': total_interest_sum,
        'paid_installments': paid_installments,
        'total_installments': total_installments,
        'total_paid_amount': total_paid_amount
    }, None


def get_loan_audit_data(loan_id):
    """Get comprehensive audit data for a loan"""
    from app.models import LoanRequest, LoanApproval, LoanRepayment, WalletTransaction

    loan = LoanRequest.query.get_or_404(loan_id)

    # Gather audit data from related models
    approvals = LoanApproval.query.filter_by(loan_id=loan_id).order_by(
        LoanApproval.voted_at.desc()
    ).all()

    repayments = LoanRepayment.query.filter_by(loan_id=loan_id).order_by(
        LoanRepayment.submitted_at.desc()
    ).all()

    # Get wallet transactions related to this loan
    transactions = []
    if loan.group and loan.group.wallet:
        transactions = WalletTransaction.query.filter(
            WalletTransaction.wallet_id == loan.group.wallet.id,
            WalletTransaction.reference_type == 'loan',
            WalletTransaction.reference_id == loan_id
        ).order_by(WalletTransaction.created_at.desc()).all()

    return {
        'loan': loan,
        'approvals': approvals,
        'repayments': repayments,
        'transactions': transactions
    }


def validate_loan_edit_data(loan, group, form_data):
    """Validate loan edit form data"""
    changes = []
    errors = []
    financial_terms_changed = False

    amount = form_data.get('amount')
    interest_rate = form_data.get('interest_rate')
    loan_duration = form_data.get('loan_duration')
    repayment_type = form_data.get('repayment_type')
    change_reason = form_data.get('change_reason', '').strip()

    if not change_reason:
        errors.append('Please provide a reason for the changes.')

    # Update loan amount (only if pending or pre-approved)
    if loan.status in [LoanStatus.PENDING.value, LoanStatus.PRE_APPROVED.value] and amount:
        try:
            new_amount = float(amount)
            if new_amount <= 0:
                errors.append('Amount must be greater than 0')
            elif new_amount != int(new_amount):
                errors.append('Amount must be a whole number (no decimals)')
            elif new_amount != loan.amount:
                changes.append(f"Amount: ₹{loan.amount} → ₹{new_amount}")
                financial_terms_changed = True
        except ValueError:
            errors.append('Invalid amount format')

    # Update loan duration
    if loan_duration:
        try:
            new_duration = int(loan_duration)
            if new_duration <= 0:
                errors.append('Loan duration must be greater than 0')
            elif new_duration < group.min_emi_duration_months:
                errors.append(f'Loan duration must be at least {group.min_emi_duration_months} months (group policy)!')
            elif loan.loan_duration_months is None or new_duration != loan.loan_duration_months:
                changes.append(f"Duration: {loan.loan_duration_months or 'N/A'} months → {new_duration} months")
                financial_terms_changed = True
        except ValueError:
            errors.append('Invalid loan duration format')

    # Update interest rate
    if interest_rate:
        try:
            new_rate = float(interest_rate)
            if new_rate < 0:
                errors.append('Interest rate cannot be negative')
            elif loan.interest_rate is None or new_rate != loan.interest_rate:
                changes.append(f"Interest rate: {loan.interest_rate or 'N/A'}% → {new_rate}%")
                financial_terms_changed = True
        except ValueError:
            errors.append('Invalid interest rate format')

    # Update repayment type
    if repayment_type and repayment_type in ['emi', 'bullet']:
        if loan.repayment_type is None or repayment_type != loan.repayment_type:
            changes.append(f"Repayment type: {loan.repayment_type or 'N/A'} → {repayment_type}")
            financial_terms_changed = True

    return changes, errors, financial_terms_changed


def get_loan_list_data(group_id, status_filter=None):
    """Get data for loan list view"""
    from app.models import LoanRequest, LoanStatus

    query = LoanRequest.query.filter_by(group_id=group_id, is_active=True)

    if status_filter:
        query = query.filter_by(status=status_filter)

    loans = query.order_by(LoanRequest.created_at.desc()).all()

    return loans


# ====================================
#  WALLET ROUTE HELPERS (NEW)
# ====================================

def get_wallet_view_data(group_id, user_id):
    """Get comprehensive data for wallet view"""
    from app.models import Group, GroupWallet, WalletTransaction, LoanRequest, LoanStatus, WithdrawalRequest, \
        WithdrawalStatus, MemberLedger
    from app.services.wallet_service import get_wallet_summary, get_member_wallet_summary
    from app.services.authorization_service import is_group_admin, is_group_member

    group = Group.query.get_or_404(group_id)

    if not is_group_member(user_id, group_id):
        return None, 'You are not a member of this group!'

    wallet = group.wallet
    if not wallet:
        return None, 'This group does not have a wallet!'

    # Get wallet summary
    try:
        summary = get_wallet_summary(wallet.id)
    except Exception as e:
        summary = None
        print(f"Error loading wallet summary: {str(e)}")

    # Get pending disbursements (for admin)
    pending_disbursements = []
    is_admin = is_group_admin(user_id, group_id)

    if is_admin:
        pending_disbursements = LoanRequest.query.filter_by(
            group_id=group_id,
            status=LoanStatus.APPROVED.value,
            is_active=True
        ).filter(LoanRequest.disbursed_at.is_(None)).all()

        pending_withdrawals = WithdrawalRequest.query.filter_by(
            group_id=group_id,
            status=WithdrawalStatus.PENDING.value
        ).order_by(WithdrawalRequest.created_at.desc()).all()
    else:
        pending_withdrawals = []

    # Get active loans
    active_loans = LoanRequest.query.filter(
        LoanRequest.group_id == group_id,
        LoanRequest.status == LoanStatus.DISBURSED.value,
        LoanRequest.is_active == True
    ).all()

    # Get recent transactions
    recent_transactions = WalletTransaction.query.filter_by(
        wallet_id=wallet.id,
        is_reversed=False
    ).order_by(WalletTransaction.created_at.desc()).limit(10).all()

    # Get member's personal summary
    personal_summary = None
    try:
        personal_summary = get_member_wallet_summary(wallet.id, user_id)
    except Exception as e:
        print(f"Error loading personal summary: {str(e)}")

    return {
        'group': group,
        'wallet': wallet,
        'summary': summary,
        'personal_summary': personal_summary,
        'pending_disbursements': pending_disbursements,
        'pending_withdrawals': pending_withdrawals,
        'active_loans': active_loans,
        'recent_transactions': recent_transactions,
        'is_admin': is_admin
    }, None


def get_withdraw_form_data(group_id, user_id):
    """Get data for withdrawal form"""
    from app.models import Group, GroupWallet, MemberLedger
    from app.services.withdrawal_service import get_withdrawable_amounts
    from app.services.authorization_service import is_group_member

    group = Group.query.get_or_404(group_id)

    if not is_group_member(user_id, group_id):
        return None, 'You are not a member of this group!'

    wallet = group.wallet
    if not wallet:
        return None, 'This group does not have a wallet!'

    # Get withdrawable amounts
    withdrawable = get_withdrawable_amounts(user_id, group_id)
    if 'error' in withdrawable:
        return None, withdrawable['error']

    # Get member ledger
    ledger = MemberLedger.query.filter_by(
        wallet_id=wallet.id,
        user_id=user_id
    ).first()

    # Create member_balance object
    member_balance = {
        'total_balance': ledger.total_balance if ledger else 0,
        'net_principal': ledger.net_principal if ledger else 0,
        'net_interest': ledger.net_interest if ledger else 0
    }

    return {
        'group': group,
        'wallet': wallet,
        'ledger': ledger,
        'withdrawable': withdrawable,
        'member_balance': member_balance
    }, None


def validate_contribution_data(amount, description):
    """Validate contribution form data"""
    if amount <= 0:
        return False, 'Please enter a valid amount greater than zero!'

    if not description or len(description.strip()) == 0:
        description = "Contribution to group wallet"

    return True, description


def get_transactions_data(group_id, user_id, type_filter=None, page=1, per_page=20):
    """Get paginated transaction data"""
    from app.models import Group, GroupWallet, WalletTransaction
    from app.services.authorization_service import is_group_member

    group = Group.query.get_or_404(group_id)

    if not is_group_member(user_id, group_id):
        return None, 'You are not a member of this group!'

    wallet = group.wallet
    if not wallet:
        return None, 'This group does not have a wallet!'

    query = WalletTransaction.query.filter_by(
        wallet_id=wallet.id,
        is_reversed=False
    )

    if type_filter:
        query = query.filter_by(transaction_type=type_filter)

    transactions = query.order_by(
        WalletTransaction.created_at.desc()
    ).paginate(page=page, per_page=per_page, error_out=False)

    return {
        'group': group,
        'wallet': wallet,
        'transactions': transactions,
        'type_filter': type_filter
    }, None


def get_member_ledgers_data(group_id, user_id):
    """Get data for member ledgers view"""
    from app.models import Group, GroupWallet, MemberLedger
    from app.services.authorization_service import is_group_member

    group = Group.query.get_or_404(group_id)

    if not is_group_member(user_id, group_id):
        return None, 'You are not a member of this group!'

    wallet = group.wallet
    if not wallet:
        return None, 'This group does not have a wallet!'

    ledgers = MemberLedger.query.filter_by(wallet_id=wallet.id).all()

    # Calculate totals
    total_principal = sum(l.principal_contributed for l in ledgers)
    total_interest = sum(l.interest_earned for l in ledgers)

    return {
        'group': group,
        'wallet': wallet,
        'ledgers': ledgers,
        'total_principal': total_principal,
        'total_interest': total_interest
    }, None


def get_interest_distributions_data(group_id, user_id):
    """Get data for interest distributions view"""
    from app.models import Group, InterestDistribution, LoanRequest
    from app.services.authorization_service import is_group_member

    group = Group.query.get_or_404(group_id)

    if not is_group_member(user_id, group_id):
        return None, 'You are not a member of this group!'

    # Get user's distributions
    distributions = InterestDistribution.query.join(LoanRequest).filter(
        LoanRequest.group_id == group_id,
        InterestDistribution.beneficiary_id == user_id
    ).order_by(InterestDistribution.created_at.desc()).all()

    total_earned = sum(d.interest_earned for d in distributions)

    return {
        'group': group,
        'distributions': distributions,
        'total_earned': total_earned
    }, None


def get_withdrawal_history_data(group_id, user_id):
    """Get data for withdrawal history view"""
    from app.models import Group, WithdrawalRequest
    from app.services.authorization_service import is_group_member, is_group_admin

    group = Group.query.get_or_404(group_id)

    if not is_group_member(user_id, group_id):
        return None, 'You are not a member of this group!'

    # If admin, show all withdrawals; if member, show only their own
    if is_group_admin(user_id, group_id):
        withdrawals = WithdrawalRequest.query.filter_by(
            group_id=group_id
        ).order_by(WithdrawalRequest.created_at.desc()).all()
    else:
        withdrawals = WithdrawalRequest.query.filter_by(
            group_id=group_id,
            user_id=user_id
        ).order_by(WithdrawalRequest.created_at.desc()).all()

    return {
        'group': group,
        'withdrawals': withdrawals
    }, None


def get_personal_wallet_summary_data(group_id, user_id):
    """Get data for personal wallet summary view"""
    from app.models import Group, GroupWallet
    from app.services.wallet_service import get_member_wallet_summary
    from app.services.authorization_service import is_group_member

    group = Group.query.get_or_404(group_id)

    if not is_group_member(user_id, group_id):
        return None, 'You are not a member of this group!'

    wallet = group.wallet
    if not wallet:
        return None, 'This group does not have a wallet!'

    try:
        summary = get_member_wallet_summary(wallet.id, user_id)
    except Exception as e:
        summary = None
        print(f"Error loading member wallet summary: {str(e)}")

    return {
        'group': group,
        'wallet': wallet,
        'summary': summary
    }, None


def validate_withdrawal_data(amount, withdrawal_type, member_balance):
    """Validate withdrawal form data"""
    if amount <= 0:
        return False, 'Please specify an amount to withdraw!'

    if amount > member_balance['total_balance']:
        return False, f'Cannot withdraw more than your total balance of ₹{member_balance["total_balance"]}!'

    if withdrawal_type == 'principal_only' and amount > member_balance['net_principal']:
        return False, f'Cannot withdraw more principal than available (₹{member_balance["net_principal"]})!'

    return True, None


def calculate_withdrawal_amounts(amount, withdrawal_type, member_balance):
    """Calculate principal and interest amounts for withdrawal"""
    if withdrawal_type == 'principal_only':
        principal_amount = min(amount, member_balance['net_principal'])
        interest_amount = 0
    else:  # with_interest
        # Withdraw principal first, then interest
        principal_amount = min(amount, member_balance['net_principal'])
        interest_amount = min(amount - principal_amount, member_balance['net_interest'])

    return principal_amount, interest_amount