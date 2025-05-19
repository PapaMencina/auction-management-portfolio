/**
 * Form Submission Handler
 * Handles form submissions and feedback
 */
document.addEventListener('DOMContentLoaded', function() {
    // Find all forms with data-ajax-form attribute
    const ajaxForms = document.querySelectorAll('form[data-ajax-form]');
    
    ajaxForms.forEach(form => {
        form.addEventListener('submit', function(e) {
            e.preventDefault();
            
            // Show loading overlay
            const loadingMessage = this.getAttribute('data-loading-message') || 'Submitting...';
            const loadingOverlay = window.showLoading(loadingMessage);
            
            // Get form data
            const formData = new FormData(this);
            
            // Get success redirect if specified
            const successRedirect = this.getAttribute('data-success-redirect');
            
            // Send form data via fetch
            fetch(this.action, {
                method: this.method,
                body: formData,
                headers: {
                    'X-Requested-With': 'XMLHttpRequest'
                }
            })
            .then(response => {
                if (!response.ok) {
                    throw new Error('Network response was not ok');
                }
                return response.json();
            })
            .then(data => {
                // Hide loading overlay
                window.hideLoading();
                
                // Handle success
                if (data.success) {
                    // Show success message if provided
                    if (data.message) {
                        showNotification(data.message, 'success');
                    }
                    
                    // Reset form if needed
                    if (this.getAttribute('data-reset-on-success') === 'true') {
                        this.reset();
                    }
                    
                    // Redirect if specified
                    if (successRedirect) {
                        window.location.href = successRedirect;
                    }
                    
                    // Execute success callback if provided
                    const successCallback = this.getAttribute('data-success-callback');
                    if (successCallback && typeof window[successCallback] === 'function') {
                        window[successCallback](data);
                    }
                } else {
                    // Show error message
                    if (data.message) {
                        showNotification(data.message, 'error');
                    } else {
                        showNotification('An error occurred. Please try again.', 'error');
                    }
                    
                    // Handle field errors
                    if (data.errors) {
                        handleFieldErrors(form, data.errors);
                    }
                }
            })
            .catch(error => {
                // Hide loading overlay
                window.hideLoading();
                
                // Show error notification
                showNotification('An unexpected error occurred. Please try again.', 'error');
                console.error('Error:', error);
            });
        });
    });
    
    // Enhance regular forms with data-form-enhance attribute
    const enhancedForms = document.querySelectorAll('form[data-form-enhance]');
    
    enhancedForms.forEach(form => {
        // Add required field validation
        const requiredFields = form.querySelectorAll('[required]');
        
        requiredFields.forEach(field => {
            // Add visual indication for required fields
            const label = field.previousElementSibling;
            if (label && label.tagName === 'LABEL') {
                const requiredIndicator = document.createElement('span');
                requiredIndicator.className = 'text-red-500 ml-1';
                requiredIndicator.textContent = '*';
                label.appendChild(requiredIndicator);
            }
            
            // Clear error states on input
            field.addEventListener('input', function() {
                this.classList.remove('border-red-500');
                const errorEl = this.nextElementSibling;
                if (errorEl && errorEl.classList.contains('text-red-500')) {
                    errorEl.remove();
                }
            });
        });
        
        // Add form submission handling
        form.addEventListener('submit', function(e) {
            // Validate required fields
            let isValid = true;
            
            requiredFields.forEach(field => {
                if (!field.value.trim()) {
                    e.preventDefault();
                    isValid = false;
                    
                    // Add error styling
                    field.classList.add('border-red-500');
                    
                    // Add error message if not already present
                    let errorEl = field.nextElementSibling;
                    if (!errorEl || !errorEl.classList.contains('text-red-500')) {
                        errorEl = document.createElement('p');
                        errorEl.className = 'text-red-500 text-xs mt-1';
                        errorEl.textContent = 'This field is required';
                        field.insertAdjacentElement('afterend', errorEl);
                    }
                }
            });
            
            // Show loading indicator if the form is valid
            if (isValid && this.getAttribute('data-form-loading') === 'true') {
                const loadingMessage = this.getAttribute('data-loading-message') || 'Submitting...';
                window.showLoading(loadingMessage);
            }
        });
    });
    
    // Helper function to show notifications
    window.showNotification = function(message, type = 'info') {
        // Create notification element
        const notification = document.createElement('div');
        notification.className = 'fixed top-4 right-4 max-w-sm bg-white dark:bg-gray-800 rounded-lg shadow-lg overflow-hidden transform transition-all duration-300 ease-in-out z-50';
        
        // Add color based on type
        let iconClass, bgClass, textClass;
        
        switch (type) {
            case 'success':
                iconClass = 'fa-check-circle text-green-500';
                bgClass = 'bg-green-100 dark:bg-green-900/30';
                textClass = 'text-green-800 dark:text-green-300';
                break;
            case 'error':
                iconClass = 'fa-exclamation-circle text-red-500';
                bgClass = 'bg-red-100 dark:bg-red-900/30';
                textClass = 'text-red-800 dark:text-red-300';
                break;
            case 'warning':
                iconClass = 'fa-exclamation-triangle text-yellow-500';
                bgClass = 'bg-yellow-100 dark:bg-yellow-900/30';
                textClass = 'text-yellow-800 dark:text-yellow-300';
                break;
            default:
                iconClass = 'fa-info-circle text-blue-500';
                bgClass = 'bg-blue-100 dark:bg-blue-900/30';
                textClass = 'text-blue-800 dark:text-blue-300';
        }
        
        // Create content
        notification.innerHTML = `
            <div class="flex items-center p-4">
                <div class="flex-shrink-0">
                    <i class="fas ${iconClass} text-xl"></i>
                </div>
                <div class="ml-3 flex-1">
                    <p class="text-sm font-medium ${textClass}">${message}</p>
                </div>
                <div class="ml-4 flex-shrink-0 flex">
                    <button class="inline-flex text-gray-400 hover:text-gray-500 focus:outline-none">
                        <i class="fas fa-times"></i>
                    </button>
                </div>
            </div>
            <div class="h-1 ${bgClass} notification-progress"></div>
        `;
        
        // Add to DOM
        document.body.appendChild(notification);
        
        // Add progress animation
        const progress = notification.querySelector('.notification-progress');
        progress.style.width = '100%';
        progress.style.transition = 'width 5s linear';
        
        // Start animation
        setTimeout(() => {
            progress.style.width = '0%';
        }, 100);
        
        // Add close button behavior
        const closeButton = notification.querySelector('button');
        closeButton.addEventListener('click', () => {
            notification.classList.add('opacity-0', 'translate-x-full');
            setTimeout(() => {
                notification.remove();
            }, 300);
        });
        
        // Auto-remove after 5 seconds
        setTimeout(() => {
            notification.classList.add('opacity-0', 'translate-x-full');
            setTimeout(() => {
                notification.remove();
            }, 300);
        }, 5000);
        
        return notification;
    };
    
    // Helper function to handle field errors
    function handleFieldErrors(form, errors) {
        // Clear previous errors
        const existingErrors = form.querySelectorAll('.field-error');
        existingErrors.forEach(error => error.remove());
        
        // Reset error styling
        const formFields = form.querySelectorAll('input, select, textarea');
        formFields.forEach(field => {
            field.classList.remove('border-red-500');
        });
        
        // Add new errors
        for (const [fieldName, errorMessage] of Object.entries(errors)) {
            const field = form.querySelector(`[name="${fieldName}"]`);
            if (field) {
                // Add error styling
                field.classList.add('border-red-500');
                
                // Add error message
                const errorEl = document.createElement('p');
                errorEl.className = 'text-red-500 text-xs mt-1 field-error';
                errorEl.textContent = errorMessage;
                field.insertAdjacentElement('afterend', errorEl);
            }
        }
    }
});
