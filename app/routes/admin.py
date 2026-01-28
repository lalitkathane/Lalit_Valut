from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from app.models import Group, GroupMember, MemberRole
from app.services.authorization_service import is_group_admin
from app.services.helperfunctions import *

admin_bp = Blueprint('admin', __name__)


# ============== ADMIN DASHBOARD ==============
@admin_bp.route('/groups/<int:group_id>/admin')
@login_required
def admin_dashboard(group_id):
    group = get_group_or_404(group_id)

    is_admin, error_msg = require_group_admin(current_user.id, group_id)
    if not is_admin:
        flash(error_msg, 'danger')
        return redirect(url_for('groups.view_group', group_id=group_id))
    pending_loans = get_pending_loans_for_group(group_id)
    awaiting_disbursement = get_approved_loans_pending_disbursement(group_id)
    pending_repayments = get_pending_repayments_for_group(group_id)
    active_loans = get_active_loans_for_group(group_id)
    member_count = get_active_members_count(group_id)
    admins = get_admin_members(group_id)
    return render_template(
        'admin/dashboard.html',
        group=group,
        pending_loans=pending_loans,
        awaiting_disbursement=awaiting_disbursement,
        pending_repayments=pending_repayments,
        active_loans=active_loans,
        member_count=member_count,
        admins=admins
    )


# ============== PENDING REPAYMENTS ==============
@admin_bp.route('/groups/<int:group_id>/admin/repayments')
@login_required
def pending_repayments(group_id):
    group = get_group_or_404(group_id)

    is_admin, error_msg = require_group_admin(current_user.id, group_id)
    if not is_admin:
        flash(error_msg, 'danger')
        return redirect(url_for('groups.view_group', group_id=group_id))

    repayments = get_pending_repayments_for_group(group_id)

    return render_template(
        'admin/pending_repayments.html',
        group=group,
        repayments=repayments
    )


# ============== REPAYMENT DETAIL (for approval) ==============
@admin_bp.route('/admin/repayments/<int:repayment_id>')
@login_required
def repayment_detail(repayment_id):
    from app.models import LoanRepayment
    repayment = LoanRepayment.query.get_or_404(repayment_id)
    loan = repayment.loan
    group = loan.group

    is_admin, error_msg = require_group_admin(current_user.id, group.id)
    if not is_admin:
        flash(error_msg, 'danger')
        return redirect(url_for('loans.view_loan', loan_id=loan.id))

    return render_template(
        'admin/repayment_detail.html',
        repayment=repayment,
        loan=loan,
        group=group
    )


# ============== ADMIN TRANSFER HISTORY ==============
@admin_bp.route('/groups/<int:group_id>/admin/transfer-history')
@login_required
def transfer_history(group_id):
    group = get_group_or_404(group_id)

    is_admin, error_msg = require_group_admin(current_user.id, group_id)
    if not is_admin:
        flash(error_msg, 'danger')
        return redirect(url_for('groups.view_group', group_id=group_id))

    transfers = get_admin_transfer_history(group_id)

    return render_template(
        'admin/transfer_history.html',
        group=group,
        transfers=transfers
    )


# ============== PENDING WITHDRAWALS ==============
@admin_bp.route('/groups/<int:group_id>/admin/withdrawals')
@login_required
def pending_withdrawals(group_id):
    """View pending withdrawal requests"""
    group = get_group_or_404(group_id)

    is_admin, error_msg = require_group_admin(current_user.id, group_id)
    if not is_admin:
        flash(error_msg, 'danger')
        return redirect(url_for('groups.view_group', group_id=group_id))

    withdrawals = get_pending_withdrawals_for_group(group_id)

    return render_template(
        'admin/pending_withdrawals.html',
        group=group,
        withdrawals=withdrawals
    )


# ============== WITHDRAWAL DETAIL ==============
@admin_bp.route('/admin/withdrawals/<int:withdrawal_id>')
@login_required
def withdrawal_detail(withdrawal_id):
    """View withdrawal request details"""
    from app.models import WithdrawalRequest
    withdrawal = WithdrawalRequest.query.get_or_404(withdrawal_id)
    group = withdrawal.group

    is_admin, error_msg = require_group_admin(current_user.id, group.id)
    if not is_admin:
        flash(error_msg, 'danger')
        return redirect(url_for('groups.view_group', group_id=group.id))

    ledger = get_member_ledger_for_group(withdrawal.user_id, group.id)

    # Create member_balance object for the template
    member_balance = {
        'total_balance': ledger.total_balance if ledger else 0,
        'net_principal': ledger.net_principal if ledger else 0,
        'net_interest': ledger.net_interest if ledger else 0
    }

    return render_template(
        'admin/withdrawal_detail.html',
        withdrawal=withdrawal,
        group=group,
        ledger=ledger,
        member_balance=member_balance  # Add this line
    )


# ============== APPROVE WITHDRAWAL ==============
@admin_bp.route('/admin/withdrawals/<int:withdrawal_id>/approve', methods=['POST'])
@login_required
def approve_withdrawal_route(withdrawal_id):
    """Approve a withdrawal request"""
    from app.models import WithdrawalRequest
    withdrawal = WithdrawalRequest.query.get_or_404(withdrawal_id)
    group = withdrawal.group

    is_admin, error_msg = require_group_admin(current_user.id, group.id)
    if not is_admin:
        flash(error_msg, 'danger')
        return redirect(url_for('groups.view_group', group_id=group.id))

    try:
        from app.services.withdrawal_service import approve_withdrawal
        withdrawal = approve_withdrawal(withdrawal_id, current_user.id)
        flash(f'Withdrawal of ₹{withdrawal.total_amount} approved!', 'success')
    except Exception as e:
        flash(f'Error approving withdrawal: {str(e)}', 'danger')

    return redirect(url_for('admin.pending_withdrawals', group_id=group.id))


# ============== REJECT WITHDRAWAL ==============
@admin_bp.route('/admin/withdrawals/<int:withdrawal_id>/reject', methods=['POST'])
@login_required
def reject_withdrawal_route(withdrawal_id):
    """Reject a withdrawal request"""
    from app.models import WithdrawalRequest
    withdrawal = WithdrawalRequest.query.get_or_404(withdrawal_id)
    group = withdrawal.group

    is_admin, error_msg = require_group_admin(current_user.id, group.id)
    if not is_admin:
        flash(error_msg, 'danger')
        return redirect(url_for('groups.view_group', group_id=group.id))

    try:
        reason = request.form.get('reason', '')
        from app.services.withdrawal_service import reject_withdrawal
        withdrawal = reject_withdrawal(withdrawal_id, current_user.id, reason)
        flash('Withdrawal rejected.', 'warning')
    except Exception as e:
        flash(f'Error rejecting withdrawal: {str(e)}', 'danger')

    return redirect(url_for('admin.pending_withdrawals', group_id=group.id))