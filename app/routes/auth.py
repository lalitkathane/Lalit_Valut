"""
AUTHENTICATION ROUTES
=====================
"""
from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from app.extensions import db
from app.models import User
from app.services.helperfunctions import *

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/')
def home():
    if current_user.is_authenticated:
        return redirect(url_for('auth.dashboard'))
    return render_template('home.html')


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

        # Validate registration data
        error_msg = validate_user_registration_data(name, email, phone, password, confirm_password)
        if error_msg:
            flash(error_msg, 'danger')
            return redirect(url_for('auth.register'))

        # Check for existing user
        existing_user = user_exists_by_email_or_phone(email, phone)
        if existing_user:
            if existing_user.email == email:
                flash('Email already registered!', 'danger')
            else:
                flash('Phone number already registered!', 'danger')
            return redirect(url_for('auth.register'))

        # Create new user
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
        identity = request.form.get('identity')
        password = request.form.get('password')
        remember = request.form.get('remember', False)

        # Get user by email or phone
        user = get_user_by_identity(identity)

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


@auth_bp.route('/dashboard')
@login_required
def dashboard():
    """Personal dashboard"""
    try:
        # Use the member service for dashboard data
        from app.services.member_service import get_member_dashboard
        dashboard_data = get_member_dashboard(current_user.id)

        # Extract financial data
        total_contributions = dashboard_data['total_across_all_groups']['principal_contributed']
        total_interest_earned = dashboard_data['total_across_all_groups']['interest_earned']
        total_balance = dashboard_data['total_across_all_groups']['total_balance']

        # Get user data using helper functions
        groups = get_user_active_groups(current_user.id)
        active_loans = get_active_user_loans(current_user.id)
        total_outstanding = calculate_total_outstanding(active_loans)
        next_emi = get_next_emi_for_user(current_user.id)

        # Get pending approvals using helper functions
        pending_votes, pending_loan_for_vote = get_pending_votes_for_user(current_user.id)
        pending_repayment_approvals, pending_repayment_loan = get_pending_repayment_approvals_for_user(current_user.id)
        pending_withdrawal_approvals, pending_withdrawal_request = get_pending_withdrawal_approvals_for_user(current_user.id)

        return render_template(
            'dashboard.html',
            # Financial data
            total_contributions=total_contributions,
            total_interest_earned=total_interest_earned,
            total_outstanding=total_outstanding,
            total_balance=total_balance,
            # Group and loan data
            groups=groups,
            active_loans=active_loans,
            next_emi=next_emi,
            # Pending approvals
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