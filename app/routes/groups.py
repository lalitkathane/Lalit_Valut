"""
GROUP MANAGEMENT ROUTES
=======================
Clean, simplified group management.
"""
"""
GROUP MANAGEMENT ROUTES
=======================
Clean, simplified group management.
"""
from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from app.extensions import db
from app.models import Group, GroupMember, User, MemberRole, LoanRequest, LoanStatus, MemberLedger, WithdrawalRequest, \
    MemberFinancialSummary, GroupWallet
from app.services.wallet_service import create_wallet_for_group
from app.services.membership_service import (
    add_member, leave_group, remove_member, transfer_admin,
    get_member_liabilities, MembershipError, rejoin_group
)
from app.services.authorization_service import (
    is_group_admin, is_group_member, AuthorizationError, can_rejoin_group, can_leave_group
)

groups_bp = Blueprint('groups', __name__)


# ============== LIST ALL MY GROUPS ==============
@groups_bp.route('/groups')
@login_required
def list_groups():
    memberships = current_user.get_active_memberships().all()
    my_groups = [m.group for m in memberships]
    return render_template('groups/list.html', groups=my_groups)


# ============== CREATE NEW GROUP ==============
@groups_bp.route('/groups/create', methods=['GET', 'POST'])
@login_required
def create_group():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()

        if not name:
            flash('Group name is required!', 'danger')
            return redirect(url_for('groups.create_group'))

        try:
            # Create group
            new_group = Group(
                name=name,
                description=request.form.get('description', '').strip(),
                created_by=current_user.id,
                default_interest_rate=request.form.get('interest_rate', 12.0, type=float),
                default_loan_duration_months=request.form.get('loan_duration', 12, type=int),
                default_repayment_type=request.form.get('repayment_type', 'emi'),
                use_flat_rate='use_flat_rate' in request.form
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
            # REDIRECT TO ADD MEMBER PAGE INSTEAD OF VIEW GROUP
            return redirect(url_for('groups.add_member_route', group_id=new_group.id))

        except Exception as e:
            db.session.rollback()
            flash(f'Error creating group: {str(e)}', 'danger')

    return render_template('groups/create.html')


# ============== VIEW SINGLE GROUP ==============
# ============== VIEW SINGLE GROUP ==============
@groups_bp.route('/groups/<int:group_id>')
@login_required
def view_group(group_id):
    group = Group.query.get_or_404(group_id)

    if not is_group_member(current_user.id, group_id):
        flash('You are not a member of this group!', 'danger')
        return redirect(url_for('groups.list_groups'))

    # Get active members count for delete group check
    active_members_count = GroupMember.query.filter_by(
        group_id=group_id,
        is_active=True
    ).count()

    members = GroupMember.query.filter_by(group_id=group_id, is_active=True).all()
    is_admin = is_group_admin(current_user.id, group_id)

    # Pending loans
    pending_loans = LoanRequest.query.filter_by(
        group_id=group_id,
        status=LoanStatus.PENDING.value,
        is_active=True
    ).order_by(LoanRequest.created_at.desc()).all()

    # Admin-only data
    awaiting_disbursement = []
    pending_repayments = []

    if is_admin:
        awaiting_disbursement = LoanRequest.query.filter_by(
            group_id=group_id,
            status=LoanStatus.APPROVED.value,
            is_active=True
        ).filter(LoanRequest.disbursed_at.is_(None)).all()

        from app.models import LoanRepayment, RepaymentStatus
        pending_repayments = LoanRepayment.query.join(LoanRequest).filter(
            LoanRequest.group_id == group_id,
            LoanRepayment.status == RepaymentStatus.PENDING.value
        ).all()

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


# ============== GROUP SETTINGS (Admin) ==============
@groups_bp.route('/groups/<int:group_id>/settings', methods=['GET', 'POST'])
@login_required
def group_settings(group_id):
    group = Group.query.get_or_404(group_id)

    # Check admin status
    is_admin = is_group_admin(current_user.id, group_id)
    if not is_admin:
        flash('Only admin can access settings!', 'danger')
        return redirect(url_for('groups.view_group', group_id=group_id))

    if request.method == 'POST':
        group.name = request.form.get('name', group.name).strip()
        group.description = request.form.get('description', group.description).strip()
        group.default_interest_rate = request.form.get('interest_rate', group.default_interest_rate, type=float)
        group.default_loan_duration_months = request.form.get('loan_duration', group.default_loan_duration_months,
                                                              type=int)
        group.default_repayment_type = request.form.get('repayment_type', group.default_repayment_type)
        group.use_flat_rate = 'use_flat_rate' in request.form

        # Save the new EMI duration field
        group.min_emi_duration_months = request.form.get('min_emi_duration', type=int)

        db.session.commit()
        flash('Settings updated!', 'success')
        return redirect(url_for('groups.view_group', group_id=group_id))

    # Fetch data needed for the Delete Group logic in settings.html
    members = GroupMember.query.filter_by(group_id=group_id, is_active=True).all()

    return render_template(
        'groups/settings.html',
        group=group,
        is_admin=is_admin,
        members=members,  # Required for members|length check
        wallet=group.wallet  # Required for wallet.balance check
    )
# ============== ADD MEMBER ==============
@groups_bp.route('/groups/<int:group_id>/add-member', methods=['GET', 'POST'])
@login_required
def add_member_route(group_id):
    group = Group.query.get_or_404(group_id)

    if not is_group_admin(current_user.id, group_id):
        flash('Only admin can add members!', 'danger')
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
# In groups.py, update the remove_member_route:
# In groups.py, update the remove_member_route:
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
# ============== LEAVE GROUP ROUTE ==============
@groups_bp.route('/groups/<int:group_id>/leave', methods=['GET', 'POST'])
@login_required
def leave_group_route(group_id):
    """Member leaves group"""
    group = Group.query.get_or_404(group_id)

    if not is_group_member(current_user.id, group_id):
        flash('You are not a member of this group!', 'danger')
        return redirect(url_for('groups.list_groups'))

    # Check if user is the last admin
    is_admin = is_group_admin(current_user.id, group_id)
    if is_admin:
        admin_count = GroupMember.query.filter_by(
            group_id=group_id,
            role=MemberRole.ADMIN.value,
            is_active=True
        ).count()

        if admin_count <= 1:
            # Check if admin is the ONLY member AND wallet balance is zero
            active_members_count = GroupMember.query.filter_by(
                group_id=group_id,
                is_active=True
            ).count()

            # Get wallet balance
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
            active_members_count = GroupMember.query.filter_by(
                group_id=group_id,
                is_active=True
            ).count()

            # If admin is leaving and they're the only member AND wallet balance is zero
            # then delete the group instead of just leaving
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
    group = Group.query.get_or_404(group_id)

    if not is_group_admin(current_user.id, group_id):
        flash('Only admin can transfer rights!', 'danger')
        return redirect(url_for('groups.view_group', group_id=group_id))

    eligible_members = GroupMember.query.filter(
        GroupMember.group_id == group_id,
        GroupMember.is_active == True,
        GroupMember.role != MemberRole.ADMIN.value,
        GroupMember.user_id != current_user.id
    ).all()

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


# Update the view_member function to include withdrawal info
@groups_bp.route('/groups/<int:group_id>/member/<int:user_id>')
@login_required
def view_member(group_id, user_id):
    group = Group.query.get_or_404(group_id)

    if not is_group_member(current_user.id, group_id):
        flash('You are not a member of this group!', 'danger')
        return redirect(url_for('groups.list_groups'))

    member = User.query.get_or_404(user_id)
    membership = GroupMember.query.filter_by(
        group_id=group_id, user_id=user_id, is_active=True
    ).first_or_404()

    # Get member ledger
    ledger = MemberLedger.query.filter_by(
        wallet_id=group.wallet.id, user_id=user_id
    ).first() if group.wallet else None

    # Get loans
    loans = LoanRequest.query.filter_by(
        group_id=group_id, requested_by=user_id, is_active=True
    ).all()

    # Get withdrawal history for this member
    withdrawals = WithdrawalRequest.query.filter_by(
        group_id=group_id,
        user_id=user_id
    ).order_by(WithdrawalRequest.created_at.desc()).limit(10).all()

    # Get member's financial summary from service
    from app.services.membership_service import get_member_group_financial_summary
    financial_summary = get_member_group_financial_summary(user_id, group_id)

    # Check if current user is admin
    is_admin = is_group_admin(current_user.id, group_id)

    return render_template(
        'groups/member_profile.html',
        group=group,
        member=member,
        membership=membership,
        ledger=ledger,
        loans=loans,
        withdrawals=withdrawals,
        financial_summary=financial_summary,
        is_admin=is_admin  # Add this
    )
# In groups.py, add this route:

@groups_bp.route('/groups/<int:group_id>/delete', methods=['POST'])
@login_required
def delete_group_route(group_id):
    """Delete a group (admin only, only if no other members)"""
    group = Group.query.get_or_404(group_id)

    if not is_group_admin(current_user.id, group_id):
        flash('Only admin can delete group!', 'danger')
        return redirect(url_for('groups.view_group', group_id=group_id))

    # Get active members count
    active_members_count = GroupMember.query.filter_by(
        group_id=group_id,
        is_active=True
    ).count()

    if active_members_count > 1:
        flash('Cannot delete group - there are other active members!', 'danger')
        return redirect(url_for('groups.view_group', group_id=group_id))

    # Check if there are any active loans
    active_loans = LoanRequest.query.filter_by(
        group_id=group_id,
        is_active=True
    ).filter(
        LoanRequest.status.in_([
            LoanStatus.PENDING.value,
            LoanStatus.PRE_APPROVED.value,
            LoanStatus.APPROVED.value,
            LoanStatus.DISBURSED.value
        ])
    ).count()

    if active_loans > 0:
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


@groups_bp.route('/groups/<int:group_id>/rejoin', methods=['GET', 'POST'])
@login_required
def rejoin_group_route(group_id):
    """Re-join a group"""
    group = Group.query.get_or_404(group_id)

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