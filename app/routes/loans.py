"""
LOAN ROUTES
===========

Uses loan_service for all operations.
Implements strict state machine.
"""
from datetime import datetime
import time
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from app.extensions import db
from app.models import (
    Group, LoanRequest, LoanApproval, EMISchedule, LoanRepayment,
    LoanStatus, RepaymentStatus, WalletTransaction
)
from app.services.loan_service import (
    create_loan_request, cast_vote, get_loan_details, LoanError,
    approve_loan_with_interest
)
from app.services.authorization_service import (
    can_vote, can_repay, is_group_member, is_group_admin, AuthorizationError
)
from app.services.wallet_service import submit_repayment, WalletError
from app.helperfunctions import (
    validate_loan_creation_data,
    get_loan_view_data,
    validate_repayment_amount,
    get_repayment_form_data,
    get_my_loans_data,
    get_emi_schedule_data,
    get_loan_audit_data,
    validate_loan_edit_data,
    get_loan_list_data,
    get_group_or_404
)

loans_bp = Blueprint('loans', __name__)


# ============== CREATE LOAN REQUEST ==============
@loans_bp.route('/groups/<int:group_id>/loans/create', methods=['GET', 'POST'])
@login_required
def create_loan(group_id):
    """Create a new loan request in a group"""
    group = get_group_or_404(group_id)

    if not is_group_member(current_user.id, group_id):
        flash('You are not a member of this group!', 'danger')
        return redirect(url_for('groups.list_groups'))

    if request.method == 'POST':
        try:
            amount = float(request.form.get('amount', 0))
            reason = request.form.get('reason', '').strip()
            loan_duration = request.form.get('loan_duration', group.default_loan_duration_months, type=int)

            # Validate loan creation data
            is_valid, error_msg = validate_loan_creation_data(
                amount, reason, group.min_emi_duration_months, loan_duration
            )
            if not is_valid:
                flash(error_msg, 'danger')
                return render_template('loans/create.html', group=group)

            amount = int(amount)  # Convert to integer
            loan = create_loan_request(
                group_id=group_id,
                user_id=current_user.id,
                amount=amount,
                reason=reason
            )

            flash('Loan request submitted successfully!', 'success')
            return redirect(url_for('loans.view_loan', loan_id=loan.id))

        except LoanError as e:
            flash(str(e), 'danger')
        except AuthorizationError as e:
            flash(str(e), 'danger')
        except ValueError:
            flash('Please enter a valid amount!', 'danger')

    return render_template('loans/create.html', group=group)


# ============== VIEW LOAN DETAILS ==============
@loans_bp.route('/loans/<int:loan_id>')
@login_required
def view_loan(loan_id):
    """View detailed information about a loan"""
    # Force a fresh query from database
    loan = LoanRequest.query.get_or_404(loan_id)

    if not is_group_member(current_user.id, loan.group_id):
        flash('You are not a member of this group!', 'danger')
        return redirect(url_for('groups.list_groups'))

    # IMPORTANT: Refresh the loan object to get latest data
    db.session.refresh(loan)

    # Get all loan view data using helper
    loan_data = get_loan_view_data(loan_id, current_user.id)

    return render_template('loans/detail.html', **loan_data)


# ============== FINAL ADMIN APPROVAL ==============
@loans_bp.route('/loans/<int:loan_id>/final-approve', methods=['POST'])
@login_required
def final_approve_loan(loan_id):
    """Admin final approval for pre-approved loans"""
    loan = LoanRequest.query.get_or_404(loan_id)

    if not is_group_admin(current_user.id, loan.group_id):
        flash('Only group admin can perform final approval!', 'danger')
        return redirect(url_for('loans.view_loan', loan_id=loan_id))

    if loan.status != LoanStatus.PRE_APPROVED.value:
        flash('This loan is not in pre-approved state.', 'danger')
        return redirect(url_for('loans.view_loan', loan_id=loan_id))

    if loan.requested_by == current_user.id:
        flash('You cannot final-approve your own loan request.', 'danger')
        return redirect(url_for('loans.view_loan', loan_id=loan_id))

    loan.status = LoanStatus.APPROVED.value
    loan.approved_at = datetime.utcnow()
    db.session.commit()

    flash('Loan has been finally approved!', 'success')
    return redirect(url_for('loans.view_loan', loan_id=loan_id))


# ============== LIST LOANS IN GROUP ==============
@loans_bp.route('/groups/<int:group_id>/loans')
@login_required
def list_loans(group_id):
    """List all loans in a group with optional status filter"""
    group = get_group_or_404(group_id)

    if not is_group_member(current_user.id, group_id):
        flash('You are not a member of this group!', 'danger')
        return redirect(url_for('groups.list_groups'))

    # Filter by status if provided
    status_filter = request.args.get('status', None)

    # Get loans using helper
    loans = get_loan_list_data(group_id, status_filter)

    return render_template(
        'loans/list.html',
        group=group,
        loans=loans,
        status_filter=status_filter,
        LoanStatus=LoanStatus
    )


# ============== VOTE ON LOAN ==============
@loans_bp.route('/loans/<int:loan_id>/vote', methods=['POST'])
@login_required
def vote_loan(loan_id):
    """Cast vote on a loan request"""
    try:
        vote_value = request.form.get('vote')
        comment = request.form.get('comment', '').strip()

        if vote_value not in ['approve', 'reject']:
            flash('Invalid vote!', 'danger')
            return redirect(url_for('loans.view_loan', loan_id=loan_id))

        approved = (vote_value == 'approve')

        vote, new_status = cast_vote(
            loan_id=loan_id,
            user_id=current_user.id,
            approved=approved,
            comment=comment
        )

        if approved:
            flash('You approved this loan request!', 'success')
        else:
            flash('You rejected this loan request!', 'info')

        # Notify if status changed
        if new_status == LoanStatus.APPROVED.value:
            flash('Loan has been APPROVED by majority!', 'success')
        elif new_status == LoanStatus.REJECTED.value:
            flash('Loan has been REJECTED by majority.', 'warning')

    except LoanError as e:
        flash(str(e), 'danger')
    except AuthorizationError as e:
        flash(str(e), 'danger')

    return redirect(url_for('loans.view_loan', loan_id=loan_id))


# ============== MY LOANS DASHBOARD ==============
@loans_bp.route('/my-loans')
@login_required
def my_loans():
    """Personal dashboard showing user's loans and pending actions"""
    # Get data using helper
    loans_data = get_my_loans_data(current_user.id)

    return render_template('loans/my_loans.html', **loans_data)


# ============== SUBMIT REPAYMENT ==============
@loans_bp.route('/loans/<int:loan_id>/repay', methods=['GET', 'POST'])
@login_required
def repay_loan(loan_id):
    """Submit a repayment for a loan - WITH EMI RESTRICTIONS"""
    # Get repayment form data using helper
    form_data, error_msg = get_repayment_form_data(loan_id, current_user.id)

    if error_msg:
        flash(error_msg, 'warning' if 'already submitted' in error_msg else 'info')
        return redirect(url_for('loans.view_loan', loan_id=loan_id))

    loan = form_data['loan']
    group = form_data['group']

    # Check authorization
    allowed, reason = can_repay(current_user.id, loan_id)
    if not allowed:
        flash(reason, 'danger')
        return redirect(url_for('loans.view_loan', loan_id=loan_id))

    if request.method == 'POST':
        try:
            amount_str = request.form.get('amount', '0').strip()

            # Validate it's a number
            try:
                amount = float(amount_str)
            except ValueError:
                flash('Please enter a valid amount!', 'danger')
                return render_template('loans/repay.html', **form_data)

            # Validate repayment amount using helper
            is_valid, error_msg = validate_repayment_amount(
                amount, loan, group, form_data['paid_emis']
            )
            if not is_valid:
                flash(error_msg, 'danger')
                return render_template('loans/repay.html', **form_data)

            amount = int(amount)
            description = request.form.get('description', '').strip()
            emi_id = request.form.get('emi_id', type=int)

            repayment = submit_repayment(
                loan_id=loan_id,
                user_id=current_user.id,
                amount=amount,
                description=description,
                emi_schedule_id=emi_id
            )

            flash(f'Repayment of ₹{amount:,} submitted! Awaiting admin approval.', 'success')
            return redirect(url_for('loans.view_loan', loan_id=loan_id))

        except WalletError as e:
            flash(str(e), 'danger')
        except AuthorizationError as e:
            flash(str(e), 'danger')
        except ValueError:
            flash('Please enter a valid amount!', 'danger')

    return render_template('loans/repay.html', **form_data)


# ============== VIEW EMI SCHEDULE ==============
@loans_bp.route('/loans/<int:loan_id>/emi-schedule')
@login_required
def view_emi_schedule(loan_id):
    """View detailed EMI schedule for a loan"""
    loan = LoanRequest.query.get_or_404(loan_id)

    if not is_group_member(current_user.id, loan.group_id):
        flash('You are not a member of this group!', 'danger')
        return redirect(url_for('groups.list_groups'))

    # Get EMI schedule data using helper
    emi_data, error_msg = get_emi_schedule_data(loan_id)

    if error_msg:
        flash(error_msg, 'info')
        return redirect(url_for('loans.view_loan', loan_id=loan_id))

    return render_template('loans/emi_schedule.html', **emi_data)


# ============== VIEW REPAYMENT HISTORY ==============
@loans_bp.route('/loans/<int:loan_id>/repayments')
@login_required
def repayment_history(loan_id):
    """View repayment history for a loan"""
    loan = LoanRequest.query.get_or_404(loan_id)

    if not is_group_member(current_user.id, loan.group_id):
        flash('You are not a member of this group!', 'danger')
        return redirect(url_for('groups.list_groups'))

    repayments = LoanRepayment.query.filter_by(loan_id=loan_id).order_by(
        LoanRepayment.submitted_at.desc()
    ).all()

    return render_template(
        'loans/repayment_history.html',
        loan=loan,
        repayments=repayments
    )


# ============== CLOSE LOAN (ADMIN ONLY) ==============
@loans_bp.route('/loans/<int:loan_id>/close', methods=['POST'])
@login_required
def close_loan(loan_id):
    """Admin: Close a fully repaid loan"""
    loan = LoanRequest.query.get_or_404(loan_id)

    if not is_group_admin(current_user.id, loan.group_id):
        flash('Only group admin can close loans!', 'danger')
        return redirect(url_for('loans.view_loan', loan_id=loan_id))

    if loan.status != LoanStatus.DISBURSED.value:
        flash('Loan must be disbursed to close.', 'danger')
        return redirect(url_for('loans.view_loan', loan_id=loan_id))

    if not loan.is_fully_repaid():
        flash('Loan must be fully repaid to close.', 'danger')
        return redirect(url_for('loans.view_loan', loan_id=loan_id))

    try:
        loan.transition_to(LoanStatus.COMPLETED.value, current_user.id)
        db.session.commit()
        flash('Loan has been closed successfully!', 'success')
    except ValueError as e:
        flash(str(e), 'danger')

    return redirect(url_for('loans.view_loan', loan_id=loan_id))


# ============== EDIT LOAN (ADMIN ONLY) ==============
@loans_bp.route('/loans/<int:loan_id>/edit', methods=['POST'])
@login_required
def edit_loan(loan_id):
    """Admin: Edit loan terms with validation and EMI regeneration"""
    loan = LoanRequest.query.get_or_404(loan_id)
    group = loan.group  # Get the group for policy validation

    if not is_group_admin(current_user.id, loan.group_id):
        flash('Only group admin can edit loans!', 'danger')
        return redirect(url_for('loans.view_loan', loan_id=loan_id))

    try:
        change_reason = request.form.get('change_reason', '').strip()
        remarks = request.form.get('remarks', '').strip()
        notes = request.form.get('notes', '').strip()

        # Validate loan edit data using helper
        changes, errors, financial_terms_changed = validate_loan_edit_data(
            loan, group, request.form
        )

        if errors:
            for error in errors:
                flash(error, 'danger')
            return redirect(url_for('loans.view_loan', loan_id=loan_id))

        if not change_reason:
            flash('Please provide a reason for the changes.', 'danger')
            return redirect(url_for('loans.view_loan', loan_id=loan_id))

        # Track old values for audit
        old_amount = loan.amount
        old_interest_rate = loan.interest_rate
        old_duration = loan.loan_duration_months
        old_repayment_type = loan.repayment_type
        old_total_repayable = loan.total_repayable
        old_emi_amount = loan.emi_amount

        # ============== APPLY CHANGES ==============
        # Update loan amount (only if pending or pre-approved)
        amount = request.form.get('amount')
        if loan.status in [LoanStatus.PENDING.value, LoanStatus.PRE_APPROVED.value] and amount:
            new_amount = int(float(amount))
            if new_amount != loan.amount:
                loan.amount = new_amount
                if loan.status in [LoanStatus.PRE_APPROVED.value, LoanStatus.APPROVED.value]:
                    loan.approved_amount = new_amount

        # Update loan duration
        loan_duration = request.form.get('loan_duration')
        if loan_duration:
            new_duration = int(loan_duration)
            if loan.loan_duration_months is None or new_duration != loan.loan_duration_months:
                loan.loan_duration_months = new_duration

        # Update interest rate
        interest_rate = request.form.get('interest_rate')
        if interest_rate:
            new_rate = float(interest_rate)
            if loan.interest_rate is None or new_rate != loan.interest_rate:
                loan.interest_rate = new_rate

        # Update repayment type
        repayment_type = request.form.get('repayment_type')
        if repayment_type and repayment_type in ['emi', 'bullet']:
            if loan.repayment_type is None or repayment_type != loan.repayment_type:
                loan.repayment_type = repayment_type

        # ============== REGENERATE EMI SCHEDULE IF NEEDED ==============
        if financial_terms_changed and loan.status in [
            LoanStatus.PRE_APPROVED.value,
            LoanStatus.APPROVED.value,
            LoanStatus.DISBURSED.value
        ]:
            # Check if there are any paid EMIs
            paid_emis_count = EMISchedule.query.filter_by(
                loan_id=loan.id,
                is_paid=True
            ).count()

            if paid_emis_count > 0:
                flash(
                    'Cannot regenerate EMI schedule - some installments have already been paid. Please contact support.',
                    'danger')
                db.session.rollback()
                return redirect(url_for('loans.view_loan', loan_id=loan_id))

            # Delete all existing EMIs
            EMISchedule.query.filter_by(loan_id=loan.id).delete()
            db.session.flush()

            # Reset loan financials
            loan.total_interest = 0
            loan.total_repayable = 0
            loan.emi_amount = None
            loan.total_repaid = 0
            loan.total_principal_repaid = 0
            loan.total_interest_repaid = 0

            # Recalculate with new terms using the loan service
            approve_loan_with_interest(loan, is_regeneration=True)

            # COMMIT THE CHANGES HERE
            db.session.commit()

            changes.append("EMI schedule regenerated with new terms")

            # Log the regeneration
            print(f"✅ Loan #{loan.id} EMI schedule regenerated after term changes")
            print(f"   Old: Amount={old_amount}, Rate={old_interest_rate}%, Duration={old_duration} months")
            print(
                f"   New: Amount={loan.amount}, Rate={loan.interest_rate}%, Duration={loan.loan_duration_months} months")
            print(f"   New EMI: ₹{loan.emi_amount}, Total Repayable: ₹{loan.total_repayable}")

        # ============== UPDATE NOTES/REMARKS ==============
        if remarks:
            timestamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
            new_remark = f"[{timestamp}] ADMIN: {remarks} (Reason: {change_reason})"
            if loan.admin_remarks:
                loan.admin_remarks += '\n' + new_remark
            else:
                loan.admin_remarks = new_remark
            changes.append("Added admin remarks")

        if notes:
            timestamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
            new_note = f"[{timestamp}] USER: {notes}"
            if loan.borrower_notes:
                loan.borrower_notes += '\n' + new_note
            else:
                loan.borrower_notes = new_note
            changes.append("Added borrower notes")

        # ============== UPDATE LOAN STATUS IF NEEDED ==============
        # If loan was pre-approved and terms changed, it might need re-approval
        if financial_terms_changed and loan.status == LoanStatus.PRE_APPROVED.value:
            # Check if approval conditions are still met
            approval_count = loan.get_approval_count()
            required_approvals = loan.required_approvals

            if approval_count >= required_approvals:
                # Still has majority approval, keep as pre-approved
                changes.append("Loan maintains majority approval")
            else:
                # No longer has majority, revert to pending
                loan.status = LoanStatus.PENDING.value
                changes.append("Loan reverted to pending (lost majority approval)")

        loan.last_updated_at = datetime.utcnow()
        loan.last_updated_by = current_user.id

        # Commit all changes if not already committed during EMI regeneration
        if not financial_terms_changed or loan.status not in [
            LoanStatus.PRE_APPROVED.value,
            LoanStatus.APPROVED.value,
            LoanStatus.DISBURSED.value
        ]:
            db.session.commit()

        # Refresh the loan object to get updated values
        db.session.refresh(loan)

        if changes:
            flash(f'Loan updated successfully! Changes: {", ".join(changes)}', 'success')

            # Log detailed changes
            print(f"📝 Loan #{loan.id} updated by Admin {current_user.id}:")
            for change in changes:
                print(f"   - {change}")
        else:
            flash('No changes were made.', 'info')

    except Exception as e:
        db.session.rollback()
        flash(f'Error updating loan: {str(e)}', 'danger')
        import traceback
        print(f"❌ Error in edit_loan: {str(e)}")
        traceback.print_exc()

    # Redirect with cache busting parameter
    return redirect(url_for('loans.view_loan', loan_id=loan_id, _t=int(time.time())))


# ============== LOAN AUDIT LOGS (ADMIN ONLY) ==============
@loans_bp.route('/loans/<int:loan_id>/audit-logs')
@login_required
def loan_audit_logs(loan_id):
    """View comprehensive audit logs for a specific loan"""
    loan = LoanRequest.query.get_or_404(loan_id)

    if not is_group_admin(current_user.id, loan.group_id):
        flash('Only group admin can view audit logs!', 'danger')
        return redirect(url_for('loans.view_loan', loan_id=loan_id))

    # Get audit data using helper
    audit_data = get_loan_audit_data(loan_id)

    return render_template('loans/audit_logs.html', **audit_data)