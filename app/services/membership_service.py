"""
MEMBERSHIP SERVICE
==================

Updated for withdrawal support and member dashboard.
"""
from app.services.authorization_service import *
from app.services.helperfunctions import *

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

        # Check if already active member using helper
        existing = get_membership(user_id, group_id)

        if existing:
            raise MembershipError("User is already an active member")

        # Check for inactive membership (rejoining) using helper
        inactive_membership = get_membership_including_inactive(user_id, group_id)

        if inactive_membership and not inactive_membership.is_active:
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

        # Get memberships using helper functions
        from_membership = get_membership(from_user_id, group_id)
        to_membership = get_membership(to_user_id, group_id)

        if not from_membership or not to_membership:
            raise MembershipError("Member not found")

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

    # Check pending withdrawal requests using helper
    pending_withdrawals = get_pending_withdrawals(user_id, group_id)

    if pending_withdrawals:
        liabilities['can_leave'] = False
        liabilities['reasons'].append("You have pending withdrawal requests")
        liabilities['pending_withdrawals'] = [
            {'id': w.id, 'amount': w.total_amount}
            for w in pending_withdrawals
        ]

    # Check pending loan requests using helper
    pending_loans = get_pending_loans(user_id, group_id)

    if pending_loans:
        liabilities['can_leave'] = False
        liabilities['reasons'].append("You have pending loan requests")
        liabilities['pending_loans'] = [
            {'id': l.id, 'amount': l.amount}
            for l in pending_loans
        ]

    # Check approved/disbursed loans using helper
    active_loans = get_active_loans_for_user_group(user_id, group_id)

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

    # Check pending repayments using helper
    pending_repayments = get_pending_repayments(user_id, group_id)

    if pending_repayments:
        liabilities['can_leave'] = False
        liabilities['reasons'].append("You have pending repayments awaiting approval")
        liabilities['pending_repayments'] = [
            {'id': r.id, 'amount': r.amount}
            for r in pending_repayments
        ]

    return liabilities


# ============================================================
# LEAVE GROUP (CORRECTED WITH IMMEDIATE BALANCE UPDATE)
# ============================================================

def leave_group(group_id, user_id, reason=None):
    """
    Member leaves group.
    Archives their ledger when they leave.

    Special case: If user is the only member AND wallet balance is zero,
    allow leaving without withdrawal check (group will be deleted).
    """
    try:
        # Check authorization (includes liability check)
        allowed, error_reason = can_leave_group(user_id, group_id)
        if not allowed:
            raise AuthorizationError(error_reason)

        # Get membership using helper
        membership = get_membership(user_id, group_id)

        if not membership:
            raise MembershipError("You are not a member of this group")

        # Get wallet using helper
        wallet = get_group_wallet(group_id)
        if not wallet:
            raise MembershipError("Group wallet not found")

        # Get ACTIVE ledger only using helper
        ledger = get_user_ledger_for_group(user_id, group_id, active_only=True)

        # Check if this is the last member leaving using helper
        active_members_count = get_active_members_count(group_id)

        # Special case: If this is the only member AND wallet balance is zero
        # Allow leaving without withdrawal check
        if active_members_count == 1 and wallet.balance == 0:
            # Skip withdrawal check for this special case
            # The group will be deleted by the route handler
            pass
        else:
            # Normal case: Check if member has contributions to withdraw
            if ledger and ledger.net_principal > 0:
                raise MembershipError(
                    f"You must withdraw your contributions (₹{ledger.net_principal:.2f}) before leaving the group. "
                    f"Please create a withdrawal request first."
                )

        # ARCHIVE THE LEDGER (if exists)
        if ledger:
            ledger.archive()  # Set is_active=False

        # Soft delete membership
        membership.soft_delete(reason=reason or "Member left voluntarily")

        # Mark member's financial summary as dirty
        summary = MemberFinancialSummary.query.filter_by(user_id=user_id).first()
        if summary:
            summary.is_dirty = True

        # Update wallet timestamp
        wallet.last_recalculated_at = datetime.utcnow()

        db.session.commit()
        return True

    except (AuthorizationError, MembershipError):
        db.session.rollback()
        raise
    except Exception as e:
        db.session.rollback()
        raise MembershipError(f"Failed to leave group: {str(e)}")


# ============================================================
# REMOVE MEMBER (WITH IMMEDIATE BALANCE UPDATE)
# ============================================================

def remove_member(group_id, user_id, admin_user_id, reason="Removed by admin"):
    """
    Admin removes a member from group.
    Checks for active loans before allowing removal.
    Automatically processes withdrawal of member's principal balance.
    Archives the member's ledger after withdrawal.
    """
    try:
        # Check if admin has permission
        if not is_group_admin(admin_user_id, group_id):
            raise AuthorizationError("Only admin can remove members")

        # Check if member exists and is active using helper
        membership = get_membership(user_id, group_id)

        if not membership:
            raise MembershipError("Member not found or already inactive")

        # Check if admin is trying to remove themselves
        if user_id == admin_user_id:
            raise AuthorizationError("Cannot remove yourself as admin. Use 'Leave Group' instead.")

        # ========== CHECK FOR ACTIVE LOANS (SAME AS LEAVE_GROUP) ==========
        # Check pending loan requests using helper
        pending_loans = get_pending_loans(user_id, group_id)

        if pending_loans:
            loan_ids = ', '.join(str(l.id) for l in pending_loans)
            raise MembershipError(f"Cannot remove member - they have pending loan requests (IDs: {loan_ids})")

        # Check approved/disbursed loans using helper
        active_loans = get_active_loans_for_user_group(user_id, group_id)

        for loan in active_loans:
            remaining = loan.get_remaining_amount()
            if remaining > 0:
                raise MembershipError(
                    f"Cannot remove member - they have an outstanding loan (₹{remaining:.2f} remaining). "
                    f"Loan ID: {loan.id}"
                )

        # Check pending repayments using helper
        pending_repayments = get_pending_repayments(user_id, group_id)

        if pending_repayments:
            repayment_ids = ', '.join(str(r.id) for r in pending_repayments)
            raise MembershipError(f"Cannot remove member - they have pending repayments (IDs: {repayment_ids})")

        # ========== CHECK FOR PENDING WITHDRAWAL REQUESTS ==========
        # Using helper function
        pending_withdrawals = get_pending_withdrawals(user_id, group_id)

        if pending_withdrawals:
            withdrawal_ids = ', '.join(str(w.id) for w in pending_withdrawals)
            raise MembershipError(
                f"Cannot remove member - they have pending withdrawal requests (IDs: {withdrawal_ids})")
        # ========== END OF LOAN/WITHDRAWAL CHECKS ==========

        # Get wallet using helper
        wallet = get_group_wallet(group_id)
        if not wallet:
            raise MembershipError("Group wallet not found")

        # Get member's ACTIVE ledger using helper
        ledger = get_user_ledger_for_group(user_id, group_id, active_only=True)

        # Store member's principal balance for withdrawal
        principal_amount = 0
        if ledger:
            principal_amount = ledger.net_principal

        # If member has principal balance, process withdrawal
        if principal_amount > 0:
            # Create withdrawal request
            withdrawal = WithdrawalRequest(
                user_id=user_id,
                group_id=group_id,
                withdrawal_type='principal_only',
                principal_amount=principal_amount,
                interest_amount=0.0,
                total_amount=principal_amount,
                status='approved',  # Auto-approve since admin is removing
                approved_by=admin_user_id,
                approved_at=datetime.utcnow(),
                membership_action='deactivate',
                new_membership_status=False,
                idempotency_key=str(uuid.uuid4())
            )
            db.session.add(withdrawal)
            db.session.flush()

            # Process the withdrawal immediately
            # 1. Create WalletTransaction
            transaction = WalletTransaction(
                wallet_id=wallet.id,
                transaction_type=TransactionType.WITHDRAWAL.value,
                amount=-principal_amount,
                created_by=user_id,
                reference_type='withdrawal_request',
                reference_id=withdrawal.id,
                description=f"Withdrawal by user {user_id} (admin removal)",
                idempotency_key=WalletTransaction.generate_idempotency_key()
            )
            db.session.add(transaction)

            # 2. Update MemberLedger
            if ledger:
                ledger.withdraw(principal_amount=principal_amount, interest_amount=0)

            # 3. Update withdrawal record
            withdrawal.status = 'processed'
            withdrawal.processed_at = datetime.utcnow()
            withdrawal.transaction_id = transaction.id

            # 4. ARCHIVE THE LEDGER AFTER WITHDRAWAL
            if ledger:
                ledger.archive()

            # 5. IMMEDIATELY UPDATE WALLET BALANCE
            wallet.balance -= principal_amount
            wallet.total_withdrawn += principal_amount

            flash_message = f"Member removed and withdrawal of ₹{principal_amount:.2f} processed."
        else:
            # Just deactivate membership if no balance
            # ARCHIVE LEDGER if exists
            if ledger:
                ledger.archive()
            flash_message = "Member removed successfully."

        # Soft delete membership
        membership.soft_delete(reason=reason)

        # Mark member's financial summary as dirty
        summary = MemberFinancialSummary.query.filter_by(user_id=user_id).first()
        if summary:
            summary.is_dirty = True

        # Update wallet's last recalculated timestamp (not dirty since we updated directly)
        wallet.last_recalculated_at = datetime.utcnow()

        # Commit all changes
        db.session.commit()
        return True, flash_message

    except (AuthorizationError, MembershipError):
        db.session.rollback()
        raise
    except Exception as e:
        db.session.rollback()
        raise MembershipError(f"Failed to remove member: {str(e)}")


# ============================================================
# REJOIN GROUP (CORRECTED - CREATES NEW LEDGER)
# ============================================================
def rejoin_group(user_id, group_id, contribution_amount=0):
    """
    Re-join a group after withdrawal.
    Resets the existing ledger for fresh start (keeps same record, resets values).
    """
    try:
        # Check if already active member using helper
        existing = get_membership(user_id, group_id)

        if existing:
            raise MembershipError("User is already an active member of this group")

        # Find inactive membership using helper
        inactive_membership = get_membership_including_inactive(user_id, group_id)

        if inactive_membership and not inactive_membership.is_active:
            # Reactivate
            inactive_membership.reactivate()
            membership = inactive_membership
        else:
            # Create new membership
            membership = GroupMember(
                group_id=group_id,
                user_id=user_id,
                role='member'
            )
            db.session.add(membership)

        # Get wallet using helper
        wallet = get_group_wallet(group_id)
        if not wallet:
            raise MembershipError("Group wallet not found")

        # Find existing ledger (active or inactive) using helper
        ledger = get_user_ledger_for_group(user_id, group_id, active_only=False)

        if ledger:
            # RESET THE EXISTING LEDGER for fresh start
            ledger.principal_contributed = 0
            ledger.principal_withdrawn = 0
            ledger.interest_earned = 0
            ledger.interest_withdrawn = 0
            ledger.total_principal_ever = 0  # RESET historical totals
            ledger.total_interest_ever = 0   # RESET historical totals
            ledger.last_contribution_at = None
            ledger.last_withdrawal_at = None
            ledger.last_interest_credit_at = None
            ledger.is_active = True  # Mark as active again
        else:
            # Create new ledger if doesn't exist
            ledger = MemberLedger(
                wallet_id=wallet.id,
                user_id=user_id,
                principal_contributed=0,
                principal_withdrawn=0,
                interest_earned=0,
                interest_withdrawn=0,
                total_principal_ever=0,
                total_interest_ever=0,
                is_active=True
            )
            db.session.add(ledger)

        # If contributing new money
        if contribution_amount > 0:
            from app.services.wallet_service import contribute_to_wallet

            # Make contribution (this will update the reset ledger)
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
        return membership

    except Exception as e:
        db.session.rollback()
        raise MembershipError(f"Failed to rejoin group: {str(e)}")


# ============================================================
# GET MEMBER GROUP FINANCIAL SUMMARY (UPDATED)
# ============================================================

def get_member_group_financial_summary(user_id, group_id):
    """
    Get member's financial summary for a specific group.
    Returns ACTIVE ledger only.
    """
    # Get wallet using helper
    wallet = get_group_wallet(group_id)
    if not wallet:
        return {"error": "Group wallet not found"}

    # Get ACTIVE ledger only using helper
    ledger = get_user_ledger_for_group(user_id, group_id, active_only=True)

    if not ledger:
        return {
            'has_ledger': False,
            'is_active_member': get_membership(user_id, group_id) is not None
        }

    # Get membership using helper
    membership = get_membership(user_id, group_id)

    # Get withdrawal history using helper
    withdrawals = get_user_withdrawals(user_id, group_id)

    return {
        'has_ledger': True,
        'ledger_is_active': ledger.is_active,
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