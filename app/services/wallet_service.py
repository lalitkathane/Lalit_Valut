# ==========================================
# WALLET SERVICE - ATOMIC FINANCIAL OPERATIONS
# =============================================
from datetime import datetime
from app.extensions import db
from app.models import *
from app.services.authorization_service import *
from app.services.helperfunctions import *
import uuid


# ============================================================
# CUSTOM EXCEPTIONS
# ============================================================

class WalletError(Exception):
    """Base exception for wallet operations"""
    pass


class InsufficientBalanceError(WalletError):
    """Raised when wallet has insufficient balance"""
    pass


class InvalidAmountError(WalletError):
    """Raised when amount is invalid"""
    pass


class DuplicateTransactionError(WalletError):
    """Raised when idempotency key already exists"""
    pass


class InvalidStateError(WalletError):
    """Raised when operation is invalid for current state"""
    pass


# ============================================================
# IDEMPOTENCY HELPERS
# ============================================================

def generate_idempotency_key(prefix="txn"):
    """Generate unique idempotency key"""
    return f"{prefix}_{uuid.uuid4().hex}"


def check_idempotency(idempotency_key):
    """Check if transaction with this key already exists"""
    return WalletTransaction.query.filter_by(idempotency_key=idempotency_key).first()


# ============================================================
# WALLET CREATION
# ============================================================

def create_wallet_for_group(group_id):
    """Create wallet for a group (called on group creation)"""
    try:
        group = Group.query.get(group_id)
        if not group:
            raise ValueError(f"Group {group_id} not found")

        if group.wallet:
            raise ValueError(f"Group {group_id} already has a wallet")

        wallet = GroupWallet(
            group_id=group_id,
            balance=0.0,
            total_contributed=0.0,
            total_disbursed=0.0,
            total_interest_earned=0.0,
            total_withdrawn=0.0,
            is_dirty=False,
            last_recalculated_at=datetime.utcnow()
        )

        db.session.add(wallet)
        db.session.commit()

        return wallet

    except Exception as e:
        db.session.rollback()
        raise WalletError(f"Failed to create wallet: {str(e)}")


# ============================================================
# GET OR CREATE MEMBER LEDGER
# ============================================================

def get_or_create_member_ledger(wallet_id, user_id):
    """Get existing member ledger or create new one"""
    # Using helper function
    ledger = get_member_ledger_for_wallet(wallet_id, user_id)

    if not ledger:
        ledger = MemberLedger(
            wallet_id=wallet_id,
            user_id=user_id,
            principal_contributed=0.0,
            principal_withdrawn=0.0,
            interest_earned=0.0,
            interest_withdrawn=0.0,
            total_principal_ever=0.0,
            total_interest_ever=0.0,
            is_active=True  # Add this
        )
        db.session.add(ledger)
        db.session.flush()

    return ledger


# ============================================================
# UPDATE MEMBER FINANCIAL SUMMARY
# ============================================================

def update_member_financial_summary(user_id):
    """Mark member's financial summary as dirty for recalculation"""
    summary = MemberFinancialSummary.query.filter_by(user_id=user_id).first()
    if summary:
        summary.is_dirty = True
    else:
        # Create summary if it doesn't exist
        summary = MemberFinancialSummary(user_id=user_id)
        db.session.add(summary)


# ============================================================
# CONTRIBUTION (ATOMIC)
# ============================================================

def contribute_to_wallet(wallet_id, user_id, amount, description=None):
    """
    Make a contribution to group wallet.
    Updates MemberLedger (creates if doesn't exist).
    """
    try:
        # Validate amount
        if amount <= 0:
            raise ValueError("Contribution amount must be positive")

        # Get wallet
        wallet = GroupWallet.query.get(wallet_id)
        if not wallet:
            raise ValueError("Wallet not found")

        # Find or create ledger (don't filter by is_active)
        ledger = get_member_ledger_for_wallet(wallet_id, user_id)

        if not ledger:
            # Create new ledger WITH ALL FIELDS INITIALIZED
            ledger = MemberLedger(
                wallet_id=wallet_id,
                user_id=user_id,
                principal_contributed=0.0,  # Initialize to 0
                principal_withdrawn=0.0,  # Initialize to 0
                interest_earned=0.0,  # Initialize to 0
                interest_withdrawn=0.0,  # Initialize to 0
                total_principal_ever=0.0,  # Initialize to 0
                total_interest_ever=0.0,  # Initialize to 0
                is_active=True
            )
            db.session.add(ledger)

        # Ensure ledger is active
        ledger.is_active = True

        # Create contribution record
        contribution = MemberContribution(
            wallet_id=wallet_id,
            user_id=user_id,
            amount=amount,
            description=description
        )
        db.session.add(contribution)

        # Update ledger - ensure all fields are floats
        if ledger.principal_contributed is None:
            ledger.principal_contributed = 0.0
        if ledger.total_principal_ever is None:
            ledger.total_principal_ever = 0.0

        ledger.add_contribution(amount)

        # Create wallet transaction
        transaction = WalletTransaction(
            wallet_id=wallet_id,
            transaction_type=TransactionType.CONTRIBUTION.value,
            amount=amount,
            created_by=user_id,
            reference_type='member_contribution',
            reference_id=contribution.id,
            description=description or f"Contribution by user {user_id}",
            idempotency_key=WalletTransaction.generate_idempotency_key()
        )
        db.session.add(transaction)

        # Link contribution to transaction
        contribution.transaction_id = transaction.id

        # Mark wallet as dirty (needs recalculation)
        wallet.mark_dirty()

        # Mark member's financial summary as dirty
        update_member_financial_summary(user_id)

        db.session.commit()
        return contribution

    except Exception as e:
        db.session.rollback()
        raise


# ============================================================
# CREATE CONTRIBUTION SNAPSHOT (Called at loan approval)
# ============================================================

def create_contribution_snapshot(loan_id, borrower_id, wallet_id):
    """
    Create a snapshot of contributions at loan approval time.

    CRITICAL UPDATES:
    - Borrower is EXCLUDED from the snapshot
    - Only members with ACTIVE ledgers AND contributions > 0 are included
    - Percentages calculated from eligible ACTIVE pool only
    """
    # Get all member ledgers EXCLUDING borrower AND only ACTIVE
    ledgers = MemberLedger.query.filter(
        MemberLedger.wallet_id == wallet_id,
        MemberLedger.user_id != borrower_id,  # EXCLUDE BORROWER
        MemberLedger.principal_contributed > 0,
        MemberLedger.is_active == True  # CRITICAL: Only active members
    ).all()

    if not ledgers:
        return []

    # Calculate total eligible pool (excluding borrower)
    total_eligible_pool = sum(l.principal_contributed for l in ledgers)

    if total_eligible_pool <= 0:
        return []

    snapshots = []

    for ledger in ledgers:
        percentage = (ledger.principal_contributed / total_eligible_pool) * 100

        snapshot = LoanContributionSnapshot(
            loan_id=loan_id,
            user_id=ledger.user_id,
            contribution_amount=ledger.principal_contributed,
            contribution_percentage=percentage,
            total_eligible_pool=total_eligible_pool
        )
        db.session.add(snapshot)
        snapshots.append(snapshot)

    return snapshots


# ============================================================
# LOAN DISBURSEMENT (ATOMIC)
# ============================================================

def disburse_loan(loan_id, admin_user_id, idempotency_key=None):
    """
    Disburse approved loan from wallet.

    ATOMIC OPERATION:
    1. Validate loan state & authorization
    2. Create contribution snapshot (for interest distribution)
    3. Create wallet transaction (negative)
    4. Update loan status to DISBURSED
    5. Update wallet balance

    Returns: WalletTransaction
    """
    if not idempotency_key:
        idempotency_key = generate_idempotency_key(f"disburse_{loan_id}")

    try:
        # Get loan
        loan = LoanRequest.query.get(loan_id)
        if not loan:
            raise WalletError(f"Loan {loan_id} not found")

        # Check authorization
        allowed, reason = can_disburse(admin_user_id, loan_id)
        if not allowed:
            raise AuthorizationError(reason)

        # Check idempotency
        existing = check_idempotency(idempotency_key)
        if existing:
            raise DuplicateTransactionError("Loan already disbursed")

        # Get wallet using helper
        group = Group.query.get(loan.group_id)
        wallet = group.wallet

        disburse_amount = loan.approved_amount or loan.amount

        # Verify balance
        if wallet.balance < disburse_amount:
            raise InsufficientBalanceError(
                f"Insufficient balance. Required: ₹{disburse_amount}, Available: ₹{wallet.balance}"
            )

        # ⚠️ CREATE CONTRIBUTION SNAPSHOT (for interest distribution)
        # This freezes who gets interest and in what proportion
        create_contribution_snapshot(loan_id, loan.requested_by, wallet.id)

        # Calculate new balance
        new_balance = wallet.balance - disburse_amount

        # Create transaction
        transaction = WalletTransaction(
            wallet_id=wallet.id,
            transaction_type=TransactionType.LOAN_DISBURSEMENT.value,
            amount=-disburse_amount,
            balance_after=new_balance,
            reference_type='loan',
            reference_id=loan.id,
            created_by=admin_user_id,
            beneficiary_id=loan.requested_by,
            description=f"Loan disbursement to {loan.requester.name}",
            idempotency_key=idempotency_key
        )
        db.session.add(transaction)

        # Update loan status
        loan.status = LoanStatus.DISBURSED.value
        loan.disbursed_at = datetime.utcnow()
        loan.disbursed_by = admin_user_id

        # Update wallet
        wallet.balance = new_balance
        wallet.total_disbursed += disburse_amount
        wallet.is_dirty = False
        wallet.last_recalculated_at = datetime.utcnow()

        db.session.commit()

        return transaction

    except (WalletError, DuplicateTransactionError, InsufficientBalanceError, AuthorizationError):
        db.session.rollback()
        raise
    except Exception as e:
        db.session.rollback()
        raise WalletError(f"Disbursement failed: {str(e)}")


# ============================================================
# SUBMIT REPAYMENT (Creates pending repayment)
# ============================================================

def submit_repayment(loan_id, user_id, amount, description=None, emi_schedule_id=None):
    """
    Submit a repayment for approval.

    CRITICAL RULES:
    - Does NOT update wallet immediately
    - Repayment goes to PENDING status
    - Admin must approve before wallet updates
    - Borrower wallet is NOT auto-debited
    - Payment comes from external source (UPI/Cash/Bank)

    Returns: LoanRepayment (in PENDING status)
    """
    idempotency_key = generate_idempotency_key(f"repay_{loan_id}")

    try:
        # Validate amount
        if not amount or amount <= 0:
            raise InvalidAmountError("Repayment amount must be greater than 0")

        loan = LoanRequest.query.get(loan_id)
        if not loan:
            raise WalletError(f"Loan {loan_id} not found")

        # Check authorization
        allowed, reason = can_repay(user_id, loan_id)
        if not allowed:
            raise AuthorizationError(reason)

        # Limit to remaining amount
        remaining = loan.get_remaining_amount()
        actual_amount = min(amount, remaining)

        # Calculate principal/interest split
        if loan.total_repayable and loan.total_interest and loan.total_interest > 0:
            # Proportional split
            interest_ratio = loan.total_interest / loan.total_repayable
            interest_component = actual_amount * interest_ratio
            principal_component = actual_amount - interest_component
        else:
            principal_component = actual_amount
            interest_component = 0.0

        # Create repayment (PENDING - no wallet update yet!)
        repayment = LoanRepayment(
            loan_id=loan_id,
            paid_by=user_id,
            amount=actual_amount,
            principal_component=principal_component,
            interest_component=interest_component,
            emi_schedule_id=emi_schedule_id,
            status=RepaymentStatus.PENDING.value,
            idempotency_key=idempotency_key
        )
        db.session.add(repayment)
        db.session.commit()

        return repayment

    except (InvalidAmountError, WalletError, AuthorizationError):
        db.session.rollback()
        raise
    except Exception as e:
        db.session.rollback()
        raise WalletError(f"Failed to submit repayment: {str(e)}")


# ============================================================
# APPROVE REPAYMENT (ATOMIC - Updates wallet & distributes interest)
# ============================================================

def approve_repayment(repayment_id, admin_user_id):
    """
    Approve a pending repayment.

    ATOMIC OPERATION - On approval:
    1. Create wallet transaction for repayment
    2. Update loan repayment totals
    3. Distribute interest to eligible members (EXCLUDING BORROWER)
    4. Update member ledgers
    5. Update wallet balance
    6. Check if loan is fully repaid

    CRITICAL RULES:
    - Interest goes ONLY to non-borrower contributors
    - Uses frozen contribution snapshot from loan approval time
    - Borrower wallet is NOT touched

    Returns: (LoanRepayment, WalletTransaction, list of InterestDistributions)
    """
    try:
        # Get repayment
        repayment = LoanRepayment.query.get(repayment_id)
        if not repayment:
            raise WalletError(f"Repayment {repayment_id} not found")

        # Check authorization
        allowed, reason = can_approve_repayment(admin_user_id, repayment_id)
        if not allowed:
            raise AuthorizationError(reason)

        # Get loan and wallet
        loan = LoanRequest.query.get(repayment.loan_id)
        group = Group.query.get(loan.group_id)
        wallet = group.wallet

        # Approve repayment
        repayment.status = RepaymentStatus.APPROVED.value
        repayment.approved_by = admin_user_id
        repayment.approved_at = datetime.utcnow()

        # Create idempotency key
        idempotency_key = f"repay_approve_{repayment_id}_{uuid.uuid4().hex[:8]}"

        # Calculate new balance (principal + interest goes back to wallet)
        new_balance = wallet.balance + repayment.amount

        # Create wallet transaction
        transaction = WalletTransaction(
            wallet_id=wallet.id,
            transaction_type=TransactionType.REPAYMENT.value,
            amount=repayment.amount,
            balance_after=new_balance,
            reference_type='repayment',
            reference_id=repayment.id,
            created_by=admin_user_id,
            description=f"Loan repayment from {loan.requester.name}",
            idempotency_key=idempotency_key
        )
        db.session.add(transaction)
        db.session.flush()

        # Link transaction to repayment
        repayment.transaction_id = transaction.id

        # Update loan totals
        loan.total_principal_repaid += repayment.principal_component or 0
        loan.total_interest_repaid += repayment.interest_component or 0
        loan.total_repaid += repayment.amount

        # ⚠️ DISTRIBUTE INTEREST TO ELIGIBLE MEMBERS (EXCLUDING BORROWER)
        interest_distributions = []
        if repayment.interest_component and repayment.interest_component > 0:
            interest_distributions = distribute_interest_to_members(
                loan=loan,
                repayment=repayment,
                wallet=wallet,
                interest_amount=repayment.interest_component,
                admin_user_id=admin_user_id
            )

        # Check if loan is fully repaid
        if loan.total_repaid >= (loan.total_repayable or loan.approved_amount):
            loan.status = LoanStatus.COMPLETED.value
            loan.completed_at = datetime.utcnow()

        # Update wallet
        wallet.balance = new_balance
        wallet.total_interest_earned += repayment.interest_component or 0
        wallet.is_dirty = False
        wallet.last_recalculated_at = datetime.utcnow()

        # Update EMI schedule if applicable
        if repayment.emi_schedule_id:
            from app.models import EMISchedule
            emi = EMISchedule.query.get(repayment.emi_schedule_id)
            if emi:
                emi.is_paid = True
                emi.paid_at = datetime.utcnow()
                emi.paid_amount = repayment.amount
                emi.repayment_id = repayment.id

        db.session.commit()

        return repayment, transaction, interest_distributions

    except (WalletError, AuthorizationError):
        db.session.rollback()
        raise
    except Exception as e:
        db.session.rollback()
        raise WalletError(f"Repayment approval failed: {str(e)}")


# ============================================================
# DISTRIBUTE INTEREST TO MEMBERS (CRITICAL FUNCTION)
# ============================================================

def distribute_interest_to_members(loan, repayment, wallet, interest_amount, admin_user_id):
    """
    Distribute interest proportionally to eligible members.

    CRITICAL UPDATES:
    1. Use FROZEN contribution snapshot from loan approval time
    2. EXCLUDE the borrower completely
    3. Only distribute to members with ACTIVE ledgers
    4. Interest split based on contribution percentage at approval
    """
    distributions = []

    # Get frozen contribution snapshots using helper
    snapshots = get_contribution_snapshots_for_loan(loan.id)

    if not snapshots:
        return distributions

    # Filter: Get ONLY snapshots where member has ACTIVE ledger
    active_snapshots = []
    total_active_percentage = 0

    for snapshot in snapshots:
        # Check if member has ACTIVE ledger
        ledger = MemberLedger.query.filter_by(
            wallet_id=wallet.id,
            user_id=snapshot.user_id,
            is_active=True  # CRITICAL: Only active members get interest
        ).first()

        if ledger:
            active_snapshots.append(snapshot)
            total_active_percentage += snapshot.contribution_percentage

    if not active_snapshots:
        # No active members to distribute to
        return distributions

    # Recalculate percentages based on active members only
    for snapshot in active_snapshots:
        # Calculate adjusted percentage (relative to active pool)
        adjusted_percentage = (snapshot.contribution_percentage / total_active_percentage) * 100

        # Calculate interest share based on adjusted percentage
        interest_share = (adjusted_percentage / 100) * interest_amount

        if interest_share <= 0:
            continue

        # Create distribution record
        distribution = InterestDistribution(
            loan_id=loan.id,
            repayment_id=repayment.id,
            beneficiary_id=snapshot.user_id,
            contribution_amount=snapshot.contribution_amount,
            contribution_percentage=adjusted_percentage,  # Store adjusted percentage
            interest_earned=interest_share
        )
        db.session.add(distribution)
        db.session.flush()

        # Update member's ACTIVE ledger
        ledger = get_or_create_member_ledger(wallet.id, snapshot.user_id)
        ledger.add_interest(interest_share)

        # Update member's financial summary
        update_member_financial_summary(snapshot.user_id)

        # Create interest distribution transaction
        int_idempotency = f"interest_{repayment.id}_{snapshot.user_id}_{uuid.uuid4().hex[:8]}"

        int_txn = WalletTransaction(
            wallet_id=wallet.id,
            transaction_type=TransactionType.INTEREST_DISTRIBUTION.value,
            amount=0,
            reference_type='interest_distribution',
            reference_id=distribution.id,
            created_by=admin_user_id,
            beneficiary_id=snapshot.user_id,
            description=f"Interest credit ₹{interest_share:.2f} to {ledger.member.name}",
            idempotency_key=int_idempotency
        )
        db.session.add(int_txn)
        db.session.flush()

        distribution.transaction_id = int_txn.id
        distributions.append(distribution)

    return distributions


# ============================================================
# BALANCE RECALCULATION (AUDIT)
# ============================================================

def recalculate_wallet_balance(wallet_id):
    """Recalculate wallet balance from transaction ledger"""
    wallet = GroupWallet.query.get(wallet_id)
    if not wallet:
        raise WalletError(f"Wallet {wallet_id} not found")

    # Get all non-reversed transactions using helper
    transactions = get_wallet_transactions(wallet_id)

    # Calculate from ledger
    calculated_balance = sum(t.amount for t in transactions)

    contributions = sum(
        t.amount for t in transactions
        if t.transaction_type == TransactionType.CONTRIBUTION.value
    )

    disbursements = sum(
        abs(t.amount) for t in transactions
        if t.transaction_type == TransactionType.LOAN_DISBURSEMENT.value
    )

    withdrawals = sum(
        abs(t.amount) for t in transactions
        if t.transaction_type == TransactionType.WITHDRAWAL.value
    )

    repayments = sum(
        t.amount for t in transactions
        if t.transaction_type == TransactionType.REPAYMENT.value
    )

    previous_balance = wallet.balance
    difference = calculated_balance - previous_balance

    was_corrected = False
    if abs(difference) > 0.01:
        wallet.balance = calculated_balance
        wallet.total_contributed = contributions
        wallet.total_disbursed = disbursements
        wallet.total_withdrawn = withdrawals
        was_corrected = True

    wallet.is_dirty = False
    wallet.last_recalculated_at = datetime.utcnow()

    db.session.commit()

    return {
        'wallet_id': wallet_id,
        'previous_balance': previous_balance,
        'calculated_balance': calculated_balance,
        'difference': difference,
        'was_corrected': was_corrected,
        'total_contributed': contributions,
        'total_disbursed': disbursements,
        'total_withdrawn': withdrawals,
        'total_repaid': repayments
    }


# ============================================================
# WALLET SUMMARY
# ============================================================

def get_wallet_summary(wallet_id):
    """Get comprehensive wallet summary"""
    wallet = GroupWallet.query.get(wallet_id)
    if not wallet:
        raise WalletError(f"Wallet {wallet_id} not found")

    if wallet.is_dirty:
        recalculate_wallet_balance(wallet_id)
        wallet = GroupWallet.query.get(wallet_id)

    # Calculate totals using helper functions
    totals = get_wallet_transaction_totals(wallet_id)
    transaction_counts = get_transaction_counts_by_type(wallet_id)

    # Member ledgers
    member_ledgers = MemberLedger.query.filter_by(wallet_id=wallet_id).all()

    return {
        'wallet_id': wallet.id,
        'group_id': wallet.group_id,
        'group_name': wallet.group.name,
        'balance': wallet.balance,
        'total_contributed': totals.total_contributions or 0,
        'total_disbursed': abs(totals.total_disbursements or 0),
        'total_repaid': totals.total_repayments or 0,
        'total_withdrawn': abs(totals.total_withdrawals or 0),
        'total_interest_earned': wallet.total_interest_earned,
        'is_dirty': wallet.is_dirty,
        'last_recalculated_at': wallet.last_recalculated_at,
        'transaction_counts': transaction_counts,
        'member_ledgers': [
            {
                'user_id': l.user_id,
                'user_name': l.member.name,
                'principal_contributed': l.principal_contributed,
                'principal_withdrawn': l.principal_withdrawn,
                'net_principal': l.net_principal,
                'interest_earned': l.interest_earned,
                'interest_withdrawn': l.interest_withdrawn,
                'net_interest': l.net_interest,
                'total_balance': l.total_balance,
                'total_principal_ever': l.total_principal_ever,
                'total_interest_ever': l.total_interest_ever,
                'is_active_member': wallet.group.members.filter_by(
                    user_id=l.user_id,
                    is_active=True
                ).first() is not None
            }
            for l in member_ledgers
        ]
    }


# ============================================================
# GET MEMBER'S PERSONAL WALLET SUMMARY
# ============================================================

def get_member_wallet_summary(wallet_id, user_id):
    """Get wallet summary for a specific member"""
    wallet = GroupWallet.query.get(wallet_id)
    if not wallet:
        raise WalletError(f"Wallet {wallet_id} not found")

    # Get ledger using helper
    ledger = get_member_ledger_for_wallet(wallet_id, user_id)

    if not ledger:
        return {
            'has_ledger': False,
            'wallet_balance': wallet.balance,
            'group_name': wallet.group.name
        }

    # Get member's transactions using helper
    transactions = get_wallet_transactions(wallet_id, limit=10)

    # Filter for user's transactions
    user_transactions = [
        t for t in transactions
        if t.created_by == user_id or t.beneficiary_id == user_id
    ]

    return {
        'has_ledger': True,
        'group_id': wallet.group_id,
        'group_name': wallet.group.name,
        'wallet_balance': wallet.balance,
        'member_summary': ledger.get_dashboard_summary(),
        'recent_transactions': [
            {
                'id': t.id,
                'type': t.transaction_type,
                'amount': t.amount,
                'description': t.description,
                'created_at': t.created_at,
                'is_inflow': t.amount > 0
            }
            for t in user_transactions
        ]
    }


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def get_member_contribution_total(wallet_id, user_id):
    """Get total contribution by a member"""
    # Using helper function
    ledger = get_member_ledger_for_wallet(wallet_id, user_id)
    return ledger.principal_contributed if ledger else 0.0


def get_pending_disbursements(group_id):
    """Get approved loans pending disbursement"""
    # Using helper function
    return get_pending_disbursements_for_group(group_id)


def get_active_loans(group_id):
    """Get disbursed but not fully repaid loans"""
    # Using helper function
    return get_disbursed_loans_for_group(group_id)