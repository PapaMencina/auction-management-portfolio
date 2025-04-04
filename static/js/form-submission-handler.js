function handleFormSubmission(formId, buttonId, getAuctionId) {
    const form = document.getElementById(formId);
    const submitButton = document.getElementById(buttonId);

    form.addEventListener('submit', function(e) {
        e.preventDefault();
        const auctionId = getAuctionId();
        
        if (submitButton.dataset.processingAuctionId === auctionId) {
            alert('This auction/event is already being processed. Please wait for it to complete.');
            return;
        }

        const formData = new FormData(form);
        submitButton.disabled = true;
        submitButton.dataset.processingAuctionId = auctionId;
        submitButton.textContent = 'Processing...';
        
        showLoadingSpinner('Processing your request...');

        fetch(form.action, {
            method: 'POST',
            body: formData,
            headers: {
                'X-CSRFToken': formData.get('csrfmiddlewaretoken')
            }
        })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                throw new Error(data.error);
            }
            const statusMessageElement = document.getElementById('status-message');
            updateStatusDisplay(statusMessageElement, data);
            
            if (data.task_id) {
                checkTaskStatus(data.task_id, statusMessageElement);
            } else {
                hideLoadingSpinner();
            }
        })
        .catch(error => {
            console.error('Error:', error);
            const statusMessageElement = document.getElementById('status-message');
            showError(statusMessageElement, error.message);
            hideLoadingSpinner();
        })
        .finally(() => {
            submitButton.disabled = false;
            submitButton.dataset.processingAuctionId = '';
            submitButton.textContent = 'Submit';
        });
    });
}

function updateStatusDisplay(element, data) {
    let statusHtml = `<div class="status-container p-4 rounded-lg ${getStatusColorClass(data.state)}">`;
    statusHtml += `<div class="font-bold">${data.state}</div>`;
    
    if (data.stage) {
        statusHtml += `<div class="text-sm mt-1">Stage: ${data.stage}</div>`;
    }
    if (data.substage) {
        statusHtml += `<div class="text-sm mt-1">Current: ${data.substage}</div>`;
    }
    if (data.progress !== undefined) {
        statusHtml += `<div class="mt-2">
            <div class="w-full bg-gray-200 rounded-full h-2.5 dark:bg-gray-700">
                <div class="bg-blue-600 h-2.5 rounded-full" style="width: ${data.progress}%"></div>
            </div>
        </div>`;
    }
    if (data.message) {
        statusHtml += `<div class="mt-2">${data.message}</div>`;
    }
    if (data.error_context) {
        statusHtml += `<div class="mt-2 text-red-600">${data.error_context}</div>`;
    }
    
    statusHtml += '</div>';
    element.innerHTML = statusHtml;
}

function getStatusColorClass(state) {
    const statusColors = {
        'NOT_STARTED': 'bg-gray-100 dark:bg-gray-800',
        'IN_PROGRESS': 'bg-blue-100 dark:bg-blue-900',
        'COMPLETED': 'bg-green-100 dark:bg-green-900',
        'ERROR': 'bg-red-100 dark:bg-red-900',
        'WARNING': 'bg-yellow-100 dark:bg-yellow-900'
    };
    return statusColors[state] || statusColors['NOT_STARTED'];
}

function showError(element, message) {
    element.innerHTML = `
        <div class="bg-red-100 dark:bg-red-900 p-4 rounded-lg">
            <div class="text-red-700 dark:text-red-200">Error: ${message}</div>
        </div>
    `;
}

function checkTaskStatus(taskId, statusElement) {
    const intervalId = setInterval(() => {
        fetch(`/auction/check-task-status/${taskId}/?include_history=true`)
            .then(response => response.json())
            .then(data => {
                updateStatusDisplay(statusElement, data);
                
                if (data.state === 'COMPLETED' || data.state === 'ERROR') {
                    clearInterval(intervalId);
                    hideLoadingSpinner();
                }
            })
            .catch(error => {
                console.error('Error checking task status:', error);
                showError(statusElement, 'Failed to check task status');
                clearInterval(intervalId);
                hideLoadingSpinner();
            });
    }, 2000);
}