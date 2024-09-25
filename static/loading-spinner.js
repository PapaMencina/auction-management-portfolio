function showLoadingSpinner(message = 'Loading...') {
    const spinner = document.createElement('div');
    spinner.id = 'loading-spinner';
    spinner.innerHTML = `
        <div class="fixed top-0 left-0 w-full h-full flex items-center justify-center bg-gray-900 bg-opacity-50 z-50">
            <div class="bg-white dark:bg-gray-800 p-5 rounded-lg flex flex-col items-center">
                <div class="animate-spin rounded-full h-10 w-10 border-b-2 border-indigo-500"></div>
                <p class="mt-3 text-gray-700 dark:text-gray-300">${message}</p>
            </div>
        </div>
    `;
    document.body.appendChild(spinner);
}

function hideLoadingSpinner() {
    const spinner = document.getElementById('loading-spinner');
    if (spinner) {
        spinner.remove();
    }
}