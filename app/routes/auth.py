"""
AUTHENTICATION ROUTES
=====================
"""
from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from app.extensions import db
from app.models import User


auth_bp = Blueprint('auth', __name__)



@auth_bp.route('/')
def home():
    if current_user.is_authenticated:
        return redirect(url_for('auth.dashboard'))
    return render_template('home.html')

def validate_phone_number(phone):
    """Validate Indian mobile number"""
    import re
    # Indian mobile numbers: 6-9 followed by 9 digits
    pattern = r'^[6-9]\d{9}$'
    return bool(re.match(pattern, phone))

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('auth.dashboard'))

    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        phone = request.form.get('phone')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')

        # Validation
        if not name or not email or not phone or not password:
            flash('All fields are required!', 'danger')
            return redirect(url_for('auth.register'))

        if password != confirm_password:
            flash('Passwords do not match!', 'danger')
            return redirect(url_for('auth.register'))

        if len(password) < 6:
            flash('Password must be at least 6 characters!', 'danger')
            return redirect(url_for('auth.register'))

        # Validate phone format
        if not validate_phone_number(phone):
            flash('Please enter a valid 10-digit mobile number!', 'danger')
            return redirect(url_for('auth.register'))

        # Check for existing email or phone
        existing_user = User.query.filter((User.email == email) | (User.phone == phone)).first()
        if existing_user:
            if existing_user.email == email:
                flash('Email already registered!', 'danger')
            else:
                flash('Phone number already registered!', 'danger')
            return redirect(url_for('auth.register'))

        new_user = User(name=name, email=email, phone=phone)
        new_user.set_password(password)

        db.session.add(new_user)
        db.session.commit()

        flash('Registration successful! Please login.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('register.html')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('auth.dashboard'))

    if request.method == 'POST':
        # "login_identity" handles both Email or Phone
        identity = request.form.get('identity')
        password = request.form.get('password')
        remember = request.form.get('remember', False)

        # Search for user by email OR phone
        user = User.query.filter((User.email == identity) | (User.phone == identity)).first()

        if user and user.check_password(password):
            login_user(user, remember=remember)
            flash(f'Welcome back, {user.name}!', 'success')

            next_page = request.args.get('next')
            return redirect(next_page or url_for('auth.dashboard'))
        else:
            flash('Invalid identity or password!', 'danger')

    return render_template('login.html')


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.home'))


# In auth.py, update the dashboard function to use member_service
@auth_bp.route('/dashboard')
@login_required
def dashboard():
    """Personal dashboard using new member_service"""
    from app.services.member_service import get_member_dashboard
    from app.models import Group, GroupMember, EMISchedule, LoanRequest, LoanStatus, LoanApproval, LoanRepayment, \
        RepaymentStatus, WithdrawalRequest, WithdrawalStatus

    try:
        # Use the new member service for dashboard data
        dashboard_data = get_member_dashboard(current_user.id)

        # Extract the needed variables for the template
        total_contributions = dashboard_data['total_across_all_groups']['principal_contributed']
        total_interest_earned = dashboard_data['total_across_all_groups']['interest_earned']
        total_balance = dashboard_data['total_across_all_groups']['total_balance']

        # Get active groups
        groups = []
        for group_info in dashboard_data['active_groups']:
            group = Group.query.get(group_info['group_id'])
            if group:
                groups.append(group)

        # Get active loans (as borrower) - only loans that are not fully repaid
        active_loans = LoanRequest.query.filter_by(
            requested_by=current_user.id,
            is_active=True
        ).filter(
            LoanRequest.status == LoanStatus.DISBURSED.value,
            LoanRequest.total_repaid < LoanRequest.total_repayable  # Not fully repaid
        ).all()

        # Calculate total outstanding
        total_outstanding = sum(loan.get_remaining_amount() for loan in active_loans)

        # Get next EMI due - only for active disbursed loans that are not fully repaid
        next_emi = EMISchedule.query.join(LoanRequest).filter(
            LoanRequest.requested_by == current_user.id,
            LoanRequest.is_active == True,
            LoanRequest.status == LoanStatus.DISBURSED.value,
            LoanRequest.total_repaid < LoanRequest.total_repayable,  # Not fully repaid
            EMISchedule.is_paid == False,
            EMISchedule.due_date >= datetime.utcnow().date()
        ).order_by(EMISchedule.due_date).first()

        # Get pending votes
        pending_votes = 0
        pending_loan_for_vote = None

        memberships = current_user.get_active_memberships().all()

        for membership in memberships:
            group_loans = LoanRequest.query.filter_by(
                group_id=membership.group_id,
                status=LoanStatus.PENDING.value,
                is_active=True
            ).all()

            for loan in group_loans:
                if loan.requested_by != current_user.id:
                    existing_vote = LoanApproval.query.filter_by(
                        loan_id=loan.id,
                        user_id=current_user.id
                    ).first()
                    if not existing_vote:
                        pending_votes += 1
                        if not pending_loan_for_vote:
                            pending_loan_for_vote = loan

        # Get pending repayment approvals (for admins)
        pending_repayment_approvals = 0
        pending_repayment_loan = None

        for membership in memberships:
            if membership.role == 'admin':
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

        # Get pending withdrawal approvals (for admins)
        pending_withdrawal_approvals = 0
        pending_withdrawal_request = None

        for membership in memberships:
            if membership.role == 'admin':
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

        return render_template(
            'dashboard.html',
            # New variables for the template
            total_contributions=total_contributions,
            total_interest_earned=total_interest_earned,
            total_outstanding=total_outstanding,
            groups=groups,
            active_loans=active_loans,
            next_emi=next_emi,
            # Keep existing variables
            pending_votes=pending_votes,
            pending_repayment_approvals=pending_repayment_approvals,
            pending_withdrawal_approvals=pending_withdrawal_approvals,
            pending_loan_for_vote=pending_loan_for_vote,
            pending_repayment_loan=pending_repayment_loan,
            pending_withdrawal_request=pending_withdrawal_request,
            now=datetime.utcnow()
        )

    except Exception as e:
        flash(f'Error loading dashboard: {str(e)}', 'danger')
        return redirect(url_for('groups.list_groups'))