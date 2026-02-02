"""
GROUP MANAGEMENT ROUTES
=======================
Clean, simplified group management.
"""
from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from app.extensions import db
from app.models import Group, GroupMember, User, MemberRole, LoanRequest, MemberLedger, WithdrawalRequest, GroupWallet
from app.services.wallet_service import create_wallet_for_group
from app.services.membership_service import (
    add_member, leave_group, remove_member, transfer_admin,
    MembershipError, rejoin_group, get_member_group_financial_summary
)
from app.services.authorization_service import (
    is_group_admin, is_group_member, AuthorizationError, can_rejoin_group, is_group_admin
)
from app.services.helperfunctions import *

groups_bp = Blueprint('groups', __name__)


# ============== LIST ALL MY GROUPS ==============
@groups_bp.route('/groups')
@login_required
def list_groups():
    memberships = current_user.get_active_memberships().all()
    my_groups = [m.group for m in memberships]
    return render_template('groups/list.html', groups=my_groups)


def validate_group_description(description):
    """Validate group description length"""
    if not description or not isinstance(description, str):
        return False, "Description is required"

    description = description.strip()
    if len(description) < 10:
        return False, "Description must be at least 10 characters long"
    if len(description) > 250:
        return False, "Description cannot exceed 250 characters"

    return True, ""


def validate_group_form_data(name, description, interest_rate, loan_duration, repayment_type):
    """Validate all group form data with improved checks"""
    errors = []

    # Name validation
    name = name.strip()
    if not name:
        errors.append("Group name is required")
    elif len(name) < 3:
        errors.append("Group name must be at least 3 characters long")
    elif len(name) > 40:
        errors.append("Group name cannot exceed 40 characters")

    # Description validation - explicit 10 character check
    description = description.strip()
    if not description:
        errors.append("Description is required")
    elif len(description) < 10:
        errors.append("Description must be at least 10 characters long")
    elif len(description) > 250:
        errors.append("Description cannot exceed 250 characters")

    # Interest rate validation
    if not isinstance(interest_rate, (int, float)):
        errors.append("Interest rate must be a number")
    elif interest_rate < 0 or interest_rate > 100:
        errors.append("Interest rate must be between 0 and 100%")

    # Loan duration validation
    if not isinstance(loan_duration, int):
        errors.append("Loan duration must be a whole number")
    elif loan_duration < 1 or loan_duration > 120:
        errors.append("Loan duration must be between 1 and 120 months")

    # Repayment type validation
    if repayment_type not in ['emi', 'bullet']:
        errors.append("Invalid repayment type. Must be 'emi' or 'bullet'")

    if errors:
        return False, ". ".join(errors)

    return True, ""


# ============== CREATE NEW GROUP ==============
@groups_bp.route('/groups/create', methods=['GET', 'POST'])
@login_required
def create_group():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()
        interest_rate = request.form.get('interest_rate', 12.0, type=float)
        loan_duration = request.form.get('loan_duration', 12, type=int)
        repayment_type = request.form.get('repayment_type', 'emi')
        use_flat_rate = 'use_flat_rate' in request.form
        min_emi_duration = request.form.get('min_emi_duration', type=int) or 3

        # Validate form data
        is_valid, error_msg = validate_group_form_data(name, description, interest_rate, loan_duration, repayment_type)
        if not is_valid:
            flash(error_msg, 'danger')
            return render_template('groups/create.html',
                                   name=name,
                                   description=description,
                                   interest_rate=interest_rate,
                                   loan_duration=loan_duration,
                                   repayment_type=repayment_type,
                                   use_flat_rate=use_flat_rate,
                                   min_emi_duration=min_emi_duration)

        try:
            # Create group
            new_group = Group(
                name=name,
                description=description,
                created_by=current_user.id,
                default_interest_rate=interest_rate,
                default_loan_duration_months=loan_duration,
                default_repayment_type=repayment_type,
                use_flat_rate=use_flat_rate,
                min_emi_duration_months=min_emi_duration
            )
            db.session.add(new_group)
            db.session.flush()

            # Add creator as admin
            db.session.add(GroupMember(
                group_id=new_group.id,
                user_id=current_user.id,
                role=MemberRole.ADMIN.value
            ))
            db.session.flush()

            # Create wallet
            create_wallet_for_group(new_group.id)
            db.session.commit()

            flash(f'Group "{name}" created successfully!', 'success')
            return redirect(url_for('groups.add_member_route', group_id=new_group.id))

        except Exception as e:
            db.session.rollback()
            flash(f'Error creating group: {str(e)}', 'danger')

    return render_template('groups/create.html')


# ============== GROUP SETTINGS (Admin) ==============
@groups_bp.route('/groups/<int:group_id>/settings', methods=['GET', 'POST'])
@login_required
def group_settings(group_id):
    group = get_group_or_404(group_id)

    # Check admin status
    is_admin, error_msg = require_group_admin(current_user.id, group_id)
    if not is_admin:
        flash(error_msg, 'danger')
        return redirect(url_for('groups.view_group', group_id=group_id))

    if request.method == 'POST':
        name = request.form.get('name', group.name).strip()
        description = request.form.get('description', group.description).strip()
        interest_rate = request.form.get('interest_rate', group.default_interest_rate, type=float)
        loan_duration = request.form.get('loan_duration', group.default_loan_duration_months, type=int)
        repayment_type = request.form.get('repayment_type', group.default_repayment_type)
        use_flat_rate = 'use_flat_rate' in request.form
        min_emi_duration = request.form.get('min_emi_duration', type=int) or group.min_emi_duration_months or 3

        # Validate form data
        is_valid, error_msg = validate_group_form_data(name, description, interest_rate, loan_duration, repayment_type)
        if not is_valid:
            flash(error_msg, 'danger')
            return redirect(url_for('groups.group_settings', group_id=group_id))

        # Update group
        group.name = name
        group.description = description
        group.default_interest_rate = interest_rate
        group.default_loan_duration_months = loan_duration
        group.default_repayment_type = repayment_type
        group.use_flat_rate = use_flat_rate
        group.min_emi_duration_months = min_emi_duration

        db.session.commit()
        flash('Settings updated!', 'success')
        return redirect(url_for('groups.view_group', group_id=group_id))

    # Fetch data needed for the Delete Group logic in settings.html
    members = get_active_members(group_id)

    return render_template(
        'groups/settings.html',
        group=group,
        is_admin=is_admin,
        members=members,
        wallet=group.wallet
    )


# ============== VIEW SINGLE GROUP ==============
@groups_bp.route('/groups/<int:group_id>')
@login_required
def view_group(group_id):
    group = get_group_or_404(group_id)

    if not is_group_member(current_user.id, group_id):
        flash('You are not a member of this group!', 'danger')
        return redirect(url_for('groups.list_groups'))

    # Get active members count for delete group check
    active_members_count = get_active_members_count(group_id)
    members = get_active_members(group_id)
    is_admin = is_group_admin(current_user.id, group_id)

    # Pending loans
    pending_loans = get_pending_loans_for_group_sorted(group_id)

    # Admin-only data
    awaiting_disbursement = []
    pending_repayments = []

    if is_admin:
        awaiting_disbursement = get_group_awaiting_disbursement(group_id)
        pending_repayments = get_group_pending_repayments(group_id)

    return render_template(
        'groups/detail.html',
        group=group,
        members=members,
        is_admin=is_admin,
        wallet=group.wallet, # Required for balance check
        pending_loans=pending_loans,
        awaiting_disbursement=awaiting_disbursement,
        pending_repayments=pending_repayments,
        active_members_count=active_members_count
    )



# ============== ADD MEMBER ==============
@groups_bp.route('/groups/<int:group_id>/add-member', methods=['GET', 'POST'])
@login_required
def add_member_route(group_id):
    group = get_group_or_404(group_id)

    is_admin, error_msg = require_group_admin(current_user.id, group_id)
    if not is_admin:
        flash(error_msg, 'danger')
        return redirect(url_for('groups.view_group', group_id=group_id))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        user = User.query.filter_by(email=email).first()

        if not user:
            flash('User not found with this email!', 'danger')
        else:
            try:
                add_member(group_id, user.id, current_user.id)
                flash(f'{user.name} added to group!', 'success')
                return redirect(url_for('groups.view_group', group_id=group_id))
            except (MembershipError, AuthorizationError) as e:
                flash(str(e), 'warning')

    return render_template('groups/add_member.html', group=group)


# ============== REMOVE MEMBER ==============
@groups_bp.route('/groups/<int:group_id>/remove-member/<int:user_id>', methods=['POST'])
@login_required
def remove_member_route(group_id, user_id):
    try:
        # The function now returns (success, message) tuple
        success, message = remove_member(
            group_id=group_id,
            user_id=user_id,
            admin_user_id=current_user.id,
            reason="Removed by admin"
        )
        flash(message, 'success')
    except (MembershipError, AuthorizationError) as e:
        flash(str(e), 'danger')
    except Exception as e:
        flash(f'Error removing member: {str(e)}', 'danger')

    return redirect(url_for('groups.view_group', group_id=group_id))


# ============== LEAVE GROUP ROUTE ==============
@groups_bp.route('/groups/<int:group_id>/leave', methods=['GET', 'POST'])
@login_required
def leave_group_route(group_id):
    """Member leaves group"""
    group = get_group_or_404(group_id)

    if not is_group_member(current_user.id, group_id):
        flash('You are not a member of this group!', 'danger')
        return redirect(url_for('groups.list_groups'))

    # Check if user is the last admin
    if is_last_admin_leaving(current_user.id, group_id):
        # Check if admin is the ONLY member AND wallet balance is zero
        active_members_count = get_active_members_count(group_id)
        wallet_balance = group.wallet.balance if group.wallet else 0

        # If admin is the only member AND wallet balance is zero, allow leaving (which will delete group)
        if active_members_count == 1 and wallet_balance == 0:
            # This is okay - admin can leave and group will be deleted
            pass
        else:
            flash('Cannot leave group as you are the only admin. Transfer admin rights first or delete the group.',
                  'danger')
            return redirect(url_for('groups.view_group', group_id=group_id))

    if request.method == 'POST':
        try:
            # Check if this is the last member leaving
            active_members_count = get_active_members_count(group_id)

            # If admin is leaving and they're the only member AND wallet balance is zero
            # then delete the group instead of just leaving
            is_admin = is_group_admin(current_user.id, group_id)
            if is_admin and active_members_count == 1 and (group.wallet and group.wallet.balance == 0):
                try:
                    # Delete the group
                    group.is_active = False
                    group.deleted_at = datetime.utcnow()
                    group.deleted_by = current_user.id

                    # Soft delete wallet if exists
                    if group.wallet:
                        group.wallet.is_active = False

                    # Soft delete the membership
                    membership = GroupMember.query.filter_by(
                        group_id=group_id,
                        user_id=current_user.id,
                        is_active=True
                    ).first()
                    if membership:
                        membership.soft_delete(reason="Last admin left, group deleted")

                    db.session.commit()

                    flash(
                        f'You have left {group.name}. Since you were the only member with zero balance, the group has been deleted.',
                        'success')
                    return redirect(url_for('groups.list_groups'))

                except Exception as e:
                    db.session.rollback()
                    flash(f'Error deleting group: {str(e)}', 'danger')
                    return redirect(url_for('groups.view_group', group_id=group_id))
            else:
                # Use the normal leave_group function
                leave_group(group_id, current_user.id, "Member left voluntarily")
                flash(f'You have left {group.name}', 'success')
                return redirect(url_for('groups.list_groups'))

        except (MembershipError, AuthorizationError) as e:
            flash(str(e), 'danger')

    return render_template('groups/leave_group.html', group=group)


# ============== TRANSFER ADMIN ==============
@groups_bp.route('/groups/<int:group_id>/transfer-admin', methods=['GET', 'POST'])
@login_required
def transfer_admin_route(group_id):
    group = get_group_or_404(group_id)

    is_admin, error_msg = require_group_admin(current_user.id, group_id)
    if not is_admin:
        flash(error_msg, 'danger')
        return redirect(url_for('groups.view_group', group_id=group_id))

    eligible_members = get_eligible_members_for_admin_transfer(group_id, current_user.id)

    if request.method == 'POST':
        to_user_id = request.form.get('to_user_id', type=int)
        reason = request.form.get('reason', '').strip()

        if not to_user_id:
            flash('Please select a member!', 'danger')
        else:
            try:
                transfer_admin(group_id, current_user.id, to_user_id, reason)
                flash('Admin rights transferred!', 'success')
                return redirect(url_for('groups.view_group', group_id=group_id))
            except (MembershipError, AuthorizationError) as e:
                flash(str(e), 'danger')

    return render_template('groups/transfer_admin.html', group=group, eligible_members=eligible_members)


# ============== VIEW MEMBER PROFILE ==============
@groups_bp.route('/groups/<int:group_id>/member/<int:user_id>')
@login_required
def view_member(group_id, user_id):
    group = get_group_or_404(group_id)

    if not is_group_member(current_user.id, group_id):
        flash('You are not a member of this group!', 'danger')
        return redirect(url_for('groups.list_groups'))

    # Get member profile data using helper
    profile_data = get_member_profile_data(user_id, group_id)
    if not profile_data:
        flash('Member not found or not active in this group!', 'danger')
        return redirect(url_for('groups.view_group', group_id=group_id))

    # Get member's financial summary from service
    financial_summary = get_member_group_financial_summary(user_id, group_id)

    # Check if current user is admin
    is_admin = is_group_admin(current_user.id, group_id)

    return render_template(
        'groups/member_profile.html',
        **profile_data,
        financial_summary=financial_summary,
        is_admin=is_admin
    )


# ============== DELETE GROUP ==============
@groups_bp.route('/groups/<int:group_id>/delete', methods=['POST'])
@login_required
def delete_group_route(group_id):
    """Delete a group (admin only, only if no other members)"""
    group = get_group_or_404(group_id)

    is_admin, error_msg = require_group_admin(current_user.id, group_id)
    if not is_admin:
        flash(error_msg, 'danger')
        return redirect(url_for('groups.view_group', group_id=group_id))

    # Get active members count
    active_members_count = get_active_members_count(group_id)

    if active_members_count > 1:
        flash('Cannot delete group - there are other active members!', 'danger')
        return redirect(url_for('groups.view_group', group_id=group_id))

    # Check if there are any active loans
    if has_active_loans_in_group(group_id):
        flash('Cannot delete group - there are active loans!', 'danger')
        return redirect(url_for('groups.view_group', group_id=group_id))

    try:
        # Soft delete the group
        group.is_active = False
        group.deleted_at = datetime.utcnow()
        group.deleted_by = current_user.id

        # Also soft delete wallet if exists
        if group.wallet:
            group.wallet.is_active = False

        # Soft delete all active memberships
        GroupMember.query.filter_by(
            group_id=group_id,
            is_active=True
        ).update({'is_active': False})

        db.session.commit()

        flash(f'Group "{group.name}" has been deleted.', 'success')
        return redirect(url_for('groups.list_groups'))

    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting group: {str(e)}', 'danger')
        return redirect(url_for('groups.view_group', group_id=group_id))


# ============== REJOIN GROUP ==============
@groups_bp.route('/groups/<int:group_id>/rejoin', methods=['GET', 'POST'])
@login_required
def rejoin_group_route(group_id):
    """Re-join a group"""
    group = get_group_or_404(group_id)

    # Check authorization
    allowed, reason = can_rejoin_group(current_user.id, group_id)
    if not allowed:
        flash(reason, 'danger')
        return redirect(url_for('groups.list_groups'))

    if request.method == 'POST':
        contribution_amount = request.form.get('contribution_amount', 0, type=float)

        try:
            # Rejoin with optional contribution
            membership = rejoin_group(current_user.id, group_id, contribution_amount)

            if contribution_amount > 0:
                flash(f'Rejoined group with contribution of ₹{contribution_amount}!', 'success')
            else:
                flash('Rejoined group successfully!', 'success')

            return redirect(url_for('groups.view_group', group_id=group_id))

        except MembershipError as e:
            flash(str(e), 'danger')
        except Exception as e:
            flash(f'Error rejoining group: {str(e)}', 'danger')

    return render_template('groups/rejoin.html', group=group)