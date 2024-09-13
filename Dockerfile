# Use the official Playwright Docker image for Python
FROM mcr.microsoft.com/playwright/python:v1.39.0-focal

# Set work directory
WORKDIR /app

# Copy the requirements file and install dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . /app/

# Expose the port (Heroku uses the $PORT environment variable)
EXPOSE $PORT

# Run the application
CMD ["gunicorn", "auction_webapp.wsgi:application", "--bind", "0.0.0.0:${PORT}"]
