# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Running the Application
```bash
# Start development server
python manage.py runserver

# Start Celery worker (required for background tasks)
celery -A auction_webapp worker --loglevel=info

# Run migrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser

# Collect static files
python manage.py collectstatic --noinput
```

### Docker Development
```bash
# Start all services (web, worker, redis, minio)
docker-compose up

# Build and start
docker-compose up --build

# Stop services
docker-compose down
```

## Architecture Overview

### Project Structure
- **auction_webapp/**: Django project settings, Celery configuration, URL routing
- **auction/**: Main Django app containing models, views, tasks, and business logic
  - **scripts/**: Core automation scripts (auction_formatter.py, create_auction.py, upload_to_hibid.py, void_unpaid_on_bid.py, remove_duplicates_in_airtable.py)
  - **utils/**: Configuration management (config.json, config_manager.py), Redis utilities, progress tracking
  - **templates/**: HTML templates for web interface
  - **resources/**: Static resources (default images, logos)
- **templates/**: Base templates (base.html, login.html)
- **static/**: Static assets (CSS, JavaScript)

### Technology Stack
- **Framework**: Django 3.2.23 with custom user model (CustomUser)
- **Task Queue**: Celery 5.4.0 with Redis backend for asynchronous processing
- **Database**: PostgreSQL (production) / SQLite (development)
- **Object Storage**: MinIO (S3-compatible) for image storage
- **Browser Automation**: Playwright 1.47.0 (Chromium)
- **Deployment**: Heroku with Docker containers (separate web and worker dynos)
- **Static Files**: WhiteNoise for serving static files

### Core Models (auction/models.py)
- **CustomUser**: Extended Django user with `is_standard_user` field and `can_use_show_browser` permission
- **Event**: Central model representing auction events (event_id, warehouse, title, start_date, ending_date)
- **ImageMetadata**: Tracks images stored in MinIO (links to Event)
- **AuctionFormattedData**: Stores formatted CSV data for HiBid uploads (links to Event)
- **VoidedTransaction**: Records voided transactions (links to Event)
- **HiBidUpload**: Tracks upload status to HiBid platform (links to Event)

### Celery Task Workflow
All background tasks are defined in [auction/tasks.py](auction/tasks.py) and orchestrated via Celery:

1. **run_auction_formatter_task**: Fetches Airtable data, processes images, generates HiBid-formatted CSV
2. **create_auction_task**: Uses Playwright to create new auctions on HiBid platform
3. **void_unpaid_task**: Automates voiding unpaid transactions on HiBid
4. **remove_duplicates_task**: Identifies and removes duplicate items in Airtable

### Warehouse Configuration
Multi-warehouse support with configurations stored in [auction/utils/config.json](auction/utils/config.json):
- Each warehouse has dedicated Airtable bases, HiBid credentials, and lot prefixes
- Currently supports Maule (prefix 'M', region "88850842") and Sahara (prefix 'S', region "88850843")
- Configuration includes Airtable API keys, base IDs, view IDs, HiBid credentials, and RelayThat URLs

### Integration Points
- **Airtable**: Data source for auction items (pyairtable 2.3.3)
- **HiBid Platform**: Auction platform integration via Playwright automation
- **MinIO**: Image storage with public URL generation
- **n8n**: Webhook endpoint for HiBid upload automation (settings.N8N_HIBID_UPLOAD_ENDPOINT)
- **RelayThat**: Marketing image generation

### Key Scripts
- **auction_formatter.py**: Main data processing pipeline (Airtable → CSV with image processing)
- **create_auction.py**: Auction creation automation on HiBid platform
- **upload_to_hibid.py**: CSV upload automation to HiBid
- **void_unpaid_on_bid.py**: Transaction void processing
- **remove_duplicates_in_airtable.py**: Duplicate detection and removal

## Important Configuration

### Environment Variables
Required environment variables (see .env):
- `DJANGO_SECRET_KEY`: Django secret key
- `DJANGO_DEBUG`: Debug mode (True/False)
- `DATABASE_URL`: PostgreSQL connection string (production)
- `REDIS_URL`: Redis connection string (supports SSL with rediss://)
- Warehouse-specific credentials stored in config.json (not in environment)

### Settings Configuration
- **Time Zone**: America/Los_Angeles (PST/PDT)
- **Session Timeout**: 1 hour with browser close expiration
- **Static Files**: Served via WhiteNoise with compression
- **Authentication**: Custom user model with LOGIN_URL='/login/'
- **Celery**: Redis broker with JSON serialization, America/Los_Angeles timezone

### Deployment Configuration
- **Web Dyno**: Gunicorn with 2 workers, 4 threads per worker, gthread worker class (see [Dockerfile](Dockerfile))
- **Worker Dyno**: Celery with 8 concurrent workers, prefork pool (see [Dockerfile.worker](Dockerfile.worker))
- **Resource Limits**: Max memory per child 512MB, max tasks per child 50
- **Release Phase**: Automatic migrations and static file collection (see [heroku.yml](heroku.yml))

## Development Workflow

### Adding New Auction Operations
1. Create script in [auction/scripts/](auction/scripts/) directory
2. Add Celery task in [auction/tasks.py](auction/tasks.py) that wraps the script
3. Create view in [auction/views.py](auction/views.py) to handle web requests
4. Add URL pattern in [auction/urls.py](auction/urls.py)
5. Create template in [auction/templates/auction/](auction/templates/auction/)
6. Update warehouse configuration in [config.json](auction/utils/config.json) if needed

### Task Status Tracking
Real-time task status is managed via Redis with states: NOT_STARTED, IN_PROGRESS, COMPLETED, ERROR, WARNING. Use [auction/utils/redis_utils.py](auction/utils/redis_utils.py) for task status updates. Tasks store progress percentage, stage information, and detailed status messages.

### Image Processing Pipeline
Images are downloaded from Airtable, processed (resize, JPEG conversion, orientation correction), uploaded to MinIO, and public URLs are generated for HiBid consumption. Memory management is critical - use cleanup cycles for large batches.

### Browser Automation with Playwright
- Always use async/await patterns
- Handle timeouts gracefully (default 120s)
- Capture screenshots on errors for debugging
- Use `show_browser` parameter for debugging (requires `can_use_show_browser` permission)
- Browser sessions managed with context managers

## Notes
- The system uses aggressive memory management for image processing (see worker Dockerfile memory limits)
- Airtable API has rate limits - use caching and batch operations
- Celery tasks have 1-hour hard timeout (3600s) and 55-minute soft timeout (3300s)
- All tasks support progress callbacks for real-time status updates
- Authentication is required for all auction operations (enforced by LoginRequiredMiddleware)
- The [knowledge.md](knowledge.md) file contains comprehensive technical documentation about the entire system
