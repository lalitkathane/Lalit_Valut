// Shared validation functions for group forms
class GroupFormValidator {
    static validateDescription(description, minLength = 10, maxLength = 250) {
        const trimmed = description.trim();
        if (!trimmed) return {
            valid: false,
            message: 'Description is required'
        };
        if (trimmed.length < minLength) return {
            valid: false,
            message: `Description must be at least ${minLength} characters (currently ${trimmed.length})`
        };
        if (trimmed.length > maxLength) return {
            valid: false,
            message: `Description cannot exceed ${maxLength} characters (currently ${trimmed.length})`
        };
        return {
            valid: true,
            message: 'Description meets requirements'
        };
    }

    static setupDescriptionValidation(textareaId, charCountId, validationMsgId, minLength = 10, maxLength = 250) {
        const descInput = document.getElementById(textareaId);
        const descCount = document.getElementById(charCountId);
        const descValidation = document.getElementById(validationMsgId);

        if (!descInput || !descCount || !descValidation) {
            console.warn('Validation elements not found:', { textareaId, charCountId, validationMsgId });
            return;
        }

        const updateValidation = () => {
            const length = descInput.value.length;
            descCount.textContent = `${length} / ${maxLength}`;

            const result = this.validateDescription(descInput.value, minLength, maxLength);
            descValidation.textContent = result.message;
            descValidation.className = result.valid ?
                'validation-message validation-success' :
                'validation-message validation-error';

            if (result.valid) {
                descInput.classList.remove('is-invalid');
            } else {
                descInput.classList.add('is-invalid');
            }
        };

        descInput.addEventListener('input', updateValidation);
        updateValidation(); // Initial validation
    }

    static validateGroupName(name, minLength = 3, maxLength = 40) {
        const trimmed = name.trim();
        if (!trimmed) return {
            valid: false,
            message: 'Group name is required'
        };
        if (trimmed.length < minLength) return {
            valid: false,
            message: `Group name must be at least ${minLength} characters (currently ${trimmed.length})`
        };
        if (trimmed.length > maxLength) return {
            valid: false,
            message: `Group name cannot exceed ${maxLength} characters (currently ${trimmed.length})`
        };
        return {
            valid: true,
            message: 'Group name meets requirements'
        };
    }

    static validateForm(formId) {
        const form = document.getElementById(formId);
        if (!form) return true;

        let isValid = true;
        const errors = [];

        // Check description
        const descriptionField = form.querySelector('textarea[name="description"]');
        if (descriptionField) {
            const result = this.validateDescription(descriptionField.value);
            if (!result.valid) {
                isValid = false;
                errors.push(result.message);
                descriptionField.focus();
            }
        }

        // Check group name
        const nameField = form.querySelector('input[name="name"]');
        if (nameField) {
            const result = this.validateGroupName(nameField.value);
            if (!result.valid) {
                isValid = false;
                errors.push(result.message);
                if (isValid) nameField.focus(); // Only focus if not already focused
            }
        }

        if (!isValid) {
            alert('Please fix the following errors:\n\n' + errors.join('\n'));
        }

        return isValid;
    }
}

// Export for use in other scripts if needed
if (typeof module !== 'undefined' && module.exports) {
    module.exports = GroupFormValidator;
}