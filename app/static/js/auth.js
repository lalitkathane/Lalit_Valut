// auth.js - Common JavaScript for authentication pages

document.addEventListener('DOMContentLoaded', function() {
    // Social login buttons
    document.querySelectorAll('.auth-btn-social').forEach(btn => {
        btn.addEventListener('click', function() {
            const provider = this.getAttribute('data-provider');
            alert(`${provider.charAt(0).toUpperCase() + provider.slice(1)} authentication coming soon!`);
        });
    });

    // Auto-dismiss alerts after 5 seconds
    setTimeout(() => {
        const alerts = document.querySelectorAll('.auth-alert');
        alerts.forEach(alert => {
            if (alert.classList.contains('alert-dismissible')) {
                const closeBtn = alert.querySelector('.btn-close');
                if (closeBtn) closeBtn.click();
            }
        });
    }, 5000);

    // Common form loading state handler
    const setupFormLoadingState = (formId, submitBtnId) => {
        const form = document.getElementById(formId);
        const submitBtn = document.getElementById(submitBtnId);

        if (!form || !submitBtn) return;

        const btnText = submitBtn.querySelector('.btn-text');
        const btnLoader = submitBtn.querySelector('.btn-loader');

        if (!btnText || !btnLoader) return;

        form.addEventListener('submit', function() {
            btnText.style.display = 'none';
            btnLoader.style.display = 'inline-flex';
            submitBtn.disabled = true;
        });
    };

    // Initialize form loading states
    setupFormLoadingState('registerForm', 'submitBtn');
    setupFormLoadingState('loginForm', 'submitBtn');

    // Password strength indicator
    const passwordInput = document.getElementById('password');
    const passwordHint = document.querySelector('.auth-password-hint');

    if (passwordInput && passwordHint) {
        passwordInput.addEventListener('input', function() {
            const password = this.value;
            if (password.length === 0) {
                passwordHint.style.color = '';
                return;
            }

            const hasLetters = /[A-Za-z]/.test(password);
            const hasNumbers = /\d/.test(password);
            const isLong = password.length >= 8;

            if (isLong && hasLetters && hasNumbers) {
                passwordHint.style.color = 'var(--primary-color)';
            } else if (password.length >= 6) {
                passwordHint.style.color = 'var(--warning-color, #ffc107)';
            } else {
                passwordHint.style.color = 'var(--danger-color, #dc3545)';
            }
        });
    }

    // Forgot password link handler
    const forgotLink = document.querySelector('.auth-forgot-link');
    if (forgotLink) {
        forgotLink.addEventListener('click', function(e) {
            e.preventDefault();
            alert('Password reset feature coming soon!');
        });
    }
});