# Use an official Python runtime as a parent image
FROM python:3.10

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set work directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libatspi2.0-0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libxkbcommon0 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libnspr4 \
    libnss3 \
    libx11-xcb1 \
    libxcb-dri3-0 \
    libxss1 \
    libegl1 \
    libwayland-client0 \
    libwayland-cursor0 \
    libwayland-egl1 \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js
RUN curl -sL https://deb.nodesource.com/setup_16.x | bash -
RUN apt-get install -y nodejs

# Copy the current directory contents into the container
COPY . /app/

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright
RUN npm install playwright
RUN npx playwright install chromium

# Run the application
CMD gunicorn auction_webapp.wsgi:application --bind 0.0.0.0:$PORT --forwarded-allow-ips='*'
