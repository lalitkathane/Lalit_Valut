from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from app.services.wallet_service import (
    contribute_to_wallet, disburse_loan, approve_repayment,
    recalculate_wallet_balance, get_wallet_summary,
    WalletError, InsufficientBalanceError, InvalidAmountError,
    DuplicateTransactionError
)
from app.services.authorization_service import (
    can_contribute, can_disburse, can_approve_repayment,
    is_group_member, is_group_admin, AuthorizationError
)
from app.services.helperfunctions import *
from app.models import GroupWallet

wallet_bp = Blueprint('wallet', __name__)


# ============== VIEW WALLET ==============
@wallet_bp.route('/groups/<int:group_id>/wallet')
@login_required
def view_wallet(group_id):
    """View group wallet"""
    # Get wallet view data using helper
    wallet_data, error_msg = get_wallet_view_data(group_id, current_user.id)

    if error_msg:
        flash(error_msg, 'danger')
        return redirect(url_for('groups.list_groups'))

    return render_template('wallet/view.html', **wallet_data)


# ============== MAKE CONTRIBUTION ==============
@wallet_bp.route('/groups/<int:group_id>/wallet/contribute', methods=['GET', 'POST'])
@login_required
def contribute(group_id):
    """Make contribution to wallet"""
    group = get_group_or_404(group_id)
    wallet = group.wallet

    if not wallet:
        flash('This group does not have a wallet!', 'danger')
        return redirect(url_for('groups.view_group', group_id=group_id))

    # Check authorization
    allowed, reason = can_contribute(current_user.id, wallet.id)
    if not allowed:
        flash(reason, 'danger')
        return redirect(url_for('wallet.view_wallet', group_id=group_id))

    # Get user's ledger
    user_ledger = MemberLedger.query.filter_by(
        wallet_id=wallet.id,
        user_id=current_user.id
    ).first()

    # Get monthly contribution status
    monthly_status = get_monthly_contribution_status(current_user.id, wallet.id)

    if request.method == 'POST':
        try:
            amount = float(request.form.get('amount', 0))
            description = request.form.get('description', '')

            # Validate contribution data WITH MONTHLY LIMIT CHECK
            is_valid, validated_description = validate_contribution_data(
                amount, description, current_user.id, wallet.id
            )
            if not is_valid:
                flash(validated_description, 'danger')
                return render_template(
                    'wallet/contribute.html',
                    group=group,
                    wallet=wallet,
                    user_ledger=user_ledger
                )

            # Make contribution
            contribution = contribute_to_wallet(
                wallet_id=wallet.id,
                user_id=current_user.id,
                amount=amount,
                description=validated_description
            )

            flash(f'Successfully contributed ₹{amount:.2f}!', 'success')
            return redirect(url_for('wallet.view_wallet', group_id=group_id))

        except InvalidAmountError as e:
            flash(str(e), 'danger')
        except DuplicateTransactionError as e:
            flash('This contribution was already processed.', 'warning')
        except WalletError as e:
            flash(str(e), 'danger')
        except ValueError:
            flash('Please enter a valid amount!', 'danger')

    return render_template(
        'wallet/contribute.html',
        group=group,
        wallet=wallet,
        user_ledger=user_ledger,
        monthly_status=monthly_status  # Pass to template
    )


# ============== DISBURSE LOAN (Admin) ==============
@wallet_bp.route('/loans/<int:loan_id>/disburse', methods=['POST'])
@login_required
def disburse(loan_id):
    """Admin: Disburse loan"""
    loan = LoanRequest.query.get_or_404(loan_id)

    try:
        transaction = disburse_loan(
            loan_id=loan_id,
            admin_user_id=current_user.id
        )

        flash(f'Loan of ₹{loan.approved_amount:.2f} disbursed successfully!', 'success')

    except InsufficientBalanceError as e:
        flash(str(e), 'danger')
    except AuthorizationError as e:
        flash(str(e), 'danger')
    except DuplicateTransactionError:
        flash('This loan has already been disbursed.', 'warning')
    except WalletError as e:
        flash(str(e), 'danger')

    return redirect(url_for('loans.view_loan', loan_id=loan_id))


# ============== APPROVE REPAYMENT (Admin) ==============
@wallet_bp.route('/repayments/<int:repayment_id>/approve', methods=['POST'])
@login_required
def approve_repayment_route(repayment_id):
    """Admin: Approve repayment"""
    repayment = LoanRepayment.query.get_or_404(repayment_id)
    loan = repayment.loan

    try:
        repayment, transaction, distributions = approve_repayment(
            repayment_id=repayment_id,
            admin_user_id=current_user.id
        )

        flash(f'Repayment of ₹{repayment.amount:.2f} approved!', 'success')

        if loan.status == LoanStatus.COMPLETED.value:
            flash('🎉 Loan fully repaid and closed!', 'success')

        if distributions:
            flash(f'Interest distributed to {len(distributions)} lenders.', 'info')

    except AuthorizationError as e:
        flash(str(e), 'danger')
    except WalletError as e:
        flash(str(e), 'danger')

    return redirect(url_for('loans.view_loan', loan_id=loan.id))


# ============== REJECT REPAYMENT (Admin) ==============
@wallet_bp.route('/repayments/<int:repayment_id>/reject', methods=['POST'])
@login_required
def reject_repayment(repayment_id):
    """Admin: Reject repayment"""
    repayment = LoanRepayment.query.get_or_404(repayment_id)
    loan = repayment.loan

    # Check authorization
    allowed, reason = can_approve_repayment(current_user.id, repayment_id)
    if not allowed:
        flash(reason, 'danger')
        return redirect(url_for('loans.view_loan', loan_id=loan.id))

    try:
        rejection_reason = request.form.get('reason', '')
        repayment.reject(current_user.id, rejection_reason)
        db.session.commit()

        flash('Repayment rejected.', 'warning')

    except ValueError as e:
        flash(str(e), 'danger')
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {str(e)}', 'danger')

    return redirect(url_for('loans.view_loan', loan_id=loan.id))


# ============== TRANSACTION HISTORY ==============
@wallet_bp.route('/groups/<int:group_id>/wallet/transactions')
@login_required
def transactions(group_id):
    """View wallet transaction history"""
    # Get page parameter
    page = request.args.get('page', 1, type=int)
    type_filter = request.args.get('type', None)

    # Get transactions data using helper
    transactions_data, error_msg = get_transactions_data(
        group_id, current_user.id, type_filter, page, 20
    )

    if error_msg:
        flash(error_msg, 'danger')
        return redirect(url_for('groups.list_groups'))

    return render_template(
        'wallet/transactions.html',
        **transactions_data,
        TransactionType=TransactionType
    )


# ============== MEMBER LEDGERS ==============
@wallet_bp.route('/groups/<int:group_id>/wallet/ledgers')
@login_required
def member_ledgers(group_id):
    """View member ledgers"""
    # Get member ledgers data using helper
    ledgers_data, error_msg = get_member_ledgers_data(group_id, current_user.id)

    if error_msg:
        flash(error_msg, 'danger')
        return redirect(url_for('groups.list_groups'))

    return render_template('wallet/ledgers.html', **ledgers_data)


# ============== RECALCULATE BALANCE (Admin) ==============
@wallet_bp.route('/groups/<int:group_id>/wallet/recalculate', methods=['POST'])
@login_required
def recalculate(group_id):
    """Admin: Recalculate wallet balance"""
    group = get_group_or_404(group_id)

    is_admin, error_msg = require_group_admin(current_user.id, group_id)
    if not is_admin:
        flash(error_msg, 'danger')
        return redirect(url_for('wallet.view_wallet', group_id=group_id))

    wallet = group.wallet
    if not wallet:
        flash('This group does not have a wallet!', 'danger')
        return redirect(url_for('groups.view_group', group_id=group_id))

    try:
        result = recalculate_wallet_balance(wallet.id)

        if result['was_corrected']:
            flash(
                f"Balance corrected! Previous: ₹{result['previous_balance']:.2f}, "
                f"New: ₹{result['calculated_balance']:.2f}",
                'warning'
            )
        else:
            flash('Wallet balance verified - no correction needed.', 'success')

    except WalletError as e:
        flash(str(e), 'danger')

    return redirect(url_for('wallet.view_wallet', group_id=group_id))


# ============== INTEREST DISTRIBUTIONS ==============
@wallet_bp.route('/groups/<int:group_id>/wallet/interest-distributions')
@login_required
def interest_distributions(group_id):
    """View interest distributions"""
    # Get interest distributions data using helper
    distributions_data, error_msg = get_interest_distributions_data(group_id, current_user.id)

    if error_msg:
        flash(error_msg, 'danger')
        return redirect(url_for('groups.list_groups'))

    return render_template('wallet/interest_distributions.html', **distributions_data)


# ============== WITHDRAW FROM WALLET ==============
@wallet_bp.route('/groups/<int:group_id>/wallet/withdraw', methods=['GET', 'POST'])
@login_required
def withdraw_from_wallet(group_id):
    """Member withdraws from wallet"""
    # Get withdrawal form data using helper
    withdraw_data, error_msg = get_withdraw_form_data(group_id, current_user.id)

    if error_msg:
        flash(error_msg, 'danger')
        return redirect(url_for('wallet.view_wallet', group_id=group_id))

    if request.method == 'POST':
        try:
            amount = float(request.form.get('amount', 0))
            membership_action = request.form.get('membership_action', 'keep_active')
            reason = request.form.get('reason', '')

            # Get wallet balance for validation
            wallet = GroupWallet.query.filter_by(group_id=group_id).first()
            if not wallet:
                flash('Group wallet not found!', 'danger')
                return render_template('wallet/withdraw.html', **withdraw_data)

            # Validate withdrawal doesn't exceed wallet balance
            if amount > wallet.balance:
                flash(f'Cannot withdraw ₹{amount:.2f}. Wallet only has ₹{wallet.balance:.2f} available.', 'danger')
                return render_template('wallet/withdraw.html', **withdraw_data)

            # Validate withdrawal data
            is_valid, error_msg = validate_withdrawal_data(
                amount, withdraw_data['member_balance']
            )
            if not is_valid:
                flash(error_msg, 'danger')
                return render_template('wallet/withdraw.html', **withdraw_data)

            from app.services.withdrawal_service import create_withdrawal_request
            withdrawal = create_withdrawal_request(
                user_id=current_user.id,
                group_id=group_id,
                total_amount=amount,
                membership_action=membership_action,
                reason=reason
            )

            flash(f'Withdrawal request of ₹{withdrawal.total_amount} submitted! Awaiting admin approval.', 'success')
            return redirect(url_for('wallet.view_wallet', group_id=group_id))

        except Exception as e:
            flash(str(e), 'danger')

    return render_template('wallet/withdraw.html', **withdraw_data)


# ============== WITHDRAWAL HISTORY ==============
@wallet_bp.route('/groups/<int:group_id>/wallet/withdrawal-history')
@login_required
def withdrawal_history(group_id):
    """View withdrawal history"""
    # Get withdrawal history data using helper
    history_data, error_msg = get_withdrawal_history_data(group_id, current_user.id)

    if error_msg:
        flash(error_msg, 'danger')
        return redirect(url_for('groups.list_groups'))

    return render_template('wallet/withdrawal_history.html', **history_data)


# ============== PERSONAL WALLET SUMMARY ==============
@wallet_bp.route('/groups/<int:group_id>/wallet/my-summary')
@login_required
def personal_wallet_summary(group_id):
    """Get personal wallet summary"""
    # Get personal wallet summary data using helper
    summary_data, error_msg = get_personal_wallet_summary_data(group_id, current_user.id)

    if error_msg:
        flash(error_msg, 'danger')
        return redirect(url_for('groups.list_groups'))

    return render_template('wallet/personal_summary.html', **summary_data)