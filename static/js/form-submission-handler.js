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
            statusMessageElement.textContent = data.message;
            statusMessageElement.classList.remove('text-red-500');
            statusMessageElement.classList.add('text-green-500');
            
            if (data.task_id) {
                checkTaskStatus(data.task_id, statusMessageElement);
            } else {
                hideLoadingSpinner();
            }
        })
        .catch(error => {
            console.error('Error:', error);
            const statusMessageElement = document.getElementById('status-message');
            statusMessageElement.textContent = 'An error occurred: ' + error.message;
            statusMessageElement.classList.remove('text-green-500');
            statusMessageElement.classList.add('text-red-500');
            hideLoadingSpinner();
        })
        .finally(() => {
            submitButton.disabled = false;
            submitButton.dataset.processingAuctionId = '';
            submitButton.textContent = 'Submit';
        });
    });
}