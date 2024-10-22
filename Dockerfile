FROM mcr.microsoft.com/playwright/python:v1.39.0-jammy

WORKDIR /app

# Copy the requirements file and install dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Install only Chromium browser to save space and memory
RUN playwright install --with-deps chromium

# Copy the rest of the application code
COPY . /app/

# Set environment variable for port
ENV PORT=8000

# Command to run the web server with proper formatting
CMD gunicorn auction_webapp.wsgi:application \
    --bind 0.0.0.0:$PORT \
    --workers 2 \
    --threads 4 \
    --timeout 120 \
    --max-requests 1000 \
    --max-requests-jitter 50