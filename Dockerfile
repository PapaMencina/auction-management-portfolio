FROM mcr.microsoft.com/playwright/python:v1.39.0-jammy

WORKDIR /app

# Copy the requirements file and install dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers
RUN playwright install --with-deps

# Copy the rest of the application code
COPY . /app/

# Set environment variable for port
ENV PORT=8000

# Command to run the web server
CMD gunicorn auction_webapp.wsgi:application --bind 0.0.0.0:$PORT