/**
 * Loading Spinner Manager
 * Handles loading states and spinners across the application
 */
document.addEventListener('DOMContentLoaded', function() {
    // Find all elements with data-loading-target attribute
    const loadingTriggers = document.querySelectorAll('[data-loading-target]');
    
    // Setup loading behavior for each trigger
    loadingTriggers.forEach(trigger => {
        trigger.addEventListener('click', function(e) {
            // Get the target container
            const targetId = this.getAttribute('data-loading-target');
            const loadingMessage = this.getAttribute('data-loading-message') || 'Processing...';
            const targetContainer = document.getElementById(targetId);
            
            if (!targetContainer) return;
            
            // Create loading overlay if it doesn't exist
            let loadingOverlay = targetContainer.querySelector('.loading-overlay');
            
            if (!loadingOverlay) {
                loadingOverlay = document.createElement('div');
                loadingOverlay.className = 'loading-overlay fixed inset-0 bg-gray-900 bg-opacity-50 flex items-center justify-center z-50';
                
                const spinnerContainer = document.createElement('div');
                spinnerContainer.className = 'bg-white dark:bg-gray-800 rounded-lg shadow-lg p-6 max-w-sm w-full mx-4 text-center';
                
                const spinner = document.createElement('div');
                spinner.className = 'loading-spinner mx-auto mb-4';
                
                const message = document.createElement('p');
                message.className = 'text-gray-700 dark:text-gray-300 font-medium';
                message.textContent = loadingMessage;
                
                spinnerContainer.appendChild(spinner);
                spinnerContainer.appendChild(message);
                loadingOverlay.appendChild(spinnerContainer);
                
                // Add overlay to the document body rather than the container
                document.body.appendChild(loadingOverlay);
            }
            
            // Show loading overlay
            loadingOverlay.classList.remove('hidden');
            
            // If the click was on a form submission, let it proceed
            if (trigger.tagName === 'FORM' || trigger.closest('form')) {
                return true;
            }
        });
    });
    
    // Handle form submissions with data-form-loading attribute
    const loadingForms = document.querySelectorAll('form[data-form-loading]');
    
    loadingForms.forEach(form => {
        form.addEventListener('submit', function(e) {
            const loadingMessage = this.getAttribute('data-loading-message') || 'Submitting...';
            
            // Create loading overlay
            const loadingOverlay = document.createElement('div');
            loadingOverlay.className = 'loading-overlay fixed inset-0 bg-gray-900 bg-opacity-50 flex items-center justify-center z-50';
            
            const spinnerContainer = document.createElement('div');
            spinnerContainer.className = 'bg-white dark:bg-gray-800 rounded-lg shadow-lg p-6 max-w-sm w-full mx-4 text-center';
            
            const spinner = document.createElement('div');
            spinner.className = 'loading-spinner mx-auto mb-4';
            
            const message = document.createElement('p');
            message.className = 'text-gray-700 dark:text-gray-300 font-medium';
            message.textContent = loadingMessage;
            
            spinnerContainer.appendChild(spinner);
            spinnerContainer.appendChild(message);
            loadingOverlay.appendChild(spinnerContainer);
            
            // Add overlay to document body
            document.body.appendChild(loadingOverlay);
            
            // Let the form submission proceed
            return true;
        });
    });
    
    // Create a global loading function for JavaScript use
    window.showLoading = function(message = 'Loading...') {
        const existingOverlay = document.querySelector('.loading-overlay');
        if (existingOverlay) {
            existingOverlay.classList.remove('hidden');
            const messageEl = existingOverlay.querySelector('p');
            if (messageEl) messageEl.textContent = message;
            return existingOverlay;
        }
        
        const loadingOverlay = document.createElement('div');
        loadingOverlay.className = 'loading-overlay fixed inset-0 bg-gray-900 bg-opacity-50 flex items-center justify-center z-50';
        
        const spinnerContainer = document.createElement('div');
        spinnerContainer.className = 'bg-white dark:bg-gray-800 rounded-lg shadow-lg p-6 max-w-sm w-full mx-4 text-center';
        
        const spinner = document.createElement('div');
        spinner.className = 'loading-spinner mx-auto mb-4';
        
        const messageEl = document.createElement('p');
        messageEl.className = 'text-gray-700 dark:text-gray-300 font-medium';
        messageEl.textContent = message;
        
        spinnerContainer.appendChild(spinner);
        spinnerContainer.appendChild(messageEl);
        loadingOverlay.appendChild(spinnerContainer);
        
        document.body.appendChild(loadingOverlay);
        return loadingOverlay;
    };
    
    window.hideLoading = function() {
        const loadingOverlay = document.querySelector('.loading-overlay');
        if (loadingOverlay) {
            loadingOverlay.classList.add('hidden');
        }
    };
});