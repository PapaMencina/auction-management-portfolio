# Use the official Playwright Docker image with Python 3.10
FROM mcr.microsoft.com/playwright/python:v1.39.0-jammy

# Set work directory
WORKDIR /app

# Copy the requirements file and install dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers
RUN playwright install --with-deps

# Copy the rest of the application code
COPY . /app/

# Expose the port (Heroku uses the $PORT environment variable)
EXPOSE $PORT

# Run the application
CMD ["sh", "-c", "gunicorn auction_webapp.wsgi:application --bind 0.0.0.0:$PORT"]
