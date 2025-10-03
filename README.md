# Auction Management System

> **Note:** This is a portfolio/demonstration repository. All sensitive credentials have been removed and replaced with placeholders.

## Overview

A comprehensive Django-based web application designed to automate and manage auction operations for multiple warehouses. The system integrates with various external services to provide end-to-end auction management from data ingestion to final auction upload.

### Key Features

- 🏭 **Multi-Warehouse Support** - Manage auctions for multiple warehouse locations
- 🤖 **Automated Workflows** - Celery-based task queue for background processing
- 🖼️ **Image Processing Pipeline** - Automated image optimization and MinIO storage
- 🌐 **Browser Automation** - Playwright-powered auction platform integration
- 📊 **Data Integration** - Seamless Airtable data synchronization
- 📈 **Real-Time Progress Tracking** - Live task status updates via Redis

## Technology Stack

- **Backend**: Django 3.2.23, Python 3.x
- **Task Queue**: Celery 5.4.0 with Redis
- **Database**: PostgreSQL (production) / SQLite (development)
- **Object Storage**: MinIO (S3-compatible)
- **Browser Automation**: Playwright 1.47.0
- **Deployment**: Heroku with Docker containers
- **Frontend**: HTML, CSS, JavaScript with Bootstrap

## Architecture Highlights

### Core Components

1. **Auction Formatter** - Processes Airtable data and generates HiBid-compatible CSV files
2. **Auction Creator** - Automates auction creation on HiBid platform using Playwright
3. **Upload Manager** - Handles CSV uploads to auction platforms
4. **Transaction Processor** - Manages unpaid transaction voids
5. **Duplicate Remover** - Identifies and removes duplicate items in Airtable

### System Design

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│  Airtable   │────▶│   Django     │────▶│   HiBid     │
│   Data      │     │  Application │     │  Platform   │
└─────────────┘     └──────┬───────┘     └─────────────┘
                           │
                    ┌──────┴───────┐
                    │              │
               ┌────▼────┐    ┌───▼────┐
               │  Celery │    │  MinIO │
               │ Workers │    │ Storage│
               └─────────┘    └────────┘
```

## Quick Start

### Prerequisites

- Python 3.8+
- Redis server
- MinIO server (or S3-compatible storage)
- Docker (optional)

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/yourusername/auction-management-webapp.git
   cd auction-management-webapp
   ```

2. **Create virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   playwright install chromium
   ```

4. **Configure environment**
   ```bash
   cp .env.example .env
   cp auction/utils/config.json.example auction/utils/config.json
   # Edit these files with your credentials
   ```

5. **Run migrations**
   ```bash
   python manage.py migrate
   python manage.py createsuperuser
   ```

6. **Start services**
   ```bash
   # Terminal 1: Django development server
   python manage.py runserver

   # Terminal 2: Celery worker
   celery -A auction_webapp worker --loglevel=info

   # Terminal 3: Redis (if not running as service)
   redis-server
   ```

### Docker Deployment

```bash
docker-compose up --build
```

## Configuration

### Environment Variables (.env)

```bash
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=your-secret-key
DATABASE_URL=postgresql://user:pass@host:port/db
REDIS_URL=redis://localhost:6379/0
```

### Warehouse Configuration (auction/utils/config.json)

See `config.json.example` for the complete structure. Each warehouse requires:
- Airtable API credentials and base IDs
- HiBid platform credentials
- MinIO storage configuration
- RelayThat integration URLs

## Core Workflows

### 1. Auction Formatter
Fetches data from Airtable, processes images, and generates HiBid-compatible CSV files.

**Key Features:**
- Concurrent image processing with memory management
- Category mapping (85+ categories)
- Automated lot number generation
- Dynamic pricing logic

### 2. Auction Creation
Uses Playwright to automate auction creation on the HiBid platform.

**Process:**
- Validates auction parameters
- Navigates HiBid interface
- Creates event with specified dates/times
- Records event in local database

### 3. Upload Management
Automates CSV file uploads to auction platforms.

**Features:**
- File validation
- Progress tracking
- Error handling with screenshots
- Webhook integration (n8n)

## Development

### Project Structure

```
auction-management-webapp/
├── auction/                    # Main Django app
│   ├── scripts/               # Core automation scripts
│   ├── utils/                 # Utilities and config management
│   ├── templates/             # HTML templates
│   ├── models.py             # Database models
│   ├── views.py              # View controllers
│   └── tasks.py              # Celery tasks
├── auction_webapp/            # Django project settings
├── templates/                 # Base templates
├── static/                    # Static assets
├── Dockerfile                 # Web container
├── Dockerfile.worker          # Worker container
├── docker-compose.yml         # Local development
└── heroku.yml                # Heroku deployment
```

### Key Models

- **Event** - Represents auction events
- **ImageMetadata** - Tracks images in MinIO
- **AuctionFormattedData** - Stores CSV data
- **VoidedTransaction** - Records voided transactions
- **CustomUser** - Extended Django user model

### Testing

```bash
python manage.py test
```

## Deployment

The application is configured for Heroku deployment with separate web and worker dynos.

### Heroku Setup

```bash
heroku create your-app-name
heroku addons:create heroku-postgresql
heroku addons:create heroku-redis
heroku config:set DJANGO_SECRET_KEY=your-secret-key
git push heroku main
heroku run python manage.py migrate
```

### Resource Configuration

- **Web Dyno**: Gunicorn with 2 workers, 4 threads
- **Worker Dyno**: Celery with 8 concurrent workers
- **Memory**: Optimized for Standard-2X dynos (1GB RAM)

## Security Considerations

⚠️ **Important:** This repository has been sanitized for public sharing.

- All credentials have been replaced with placeholders
- Database file has been removed
- `.gitignore` configured to prevent credential commits
- Production secrets managed via environment variables

### Before Using This Code

1. Generate a new Django secret key
2. Create your own Airtable bases and API keys
3. Set up MinIO or S3-compatible storage
4. Configure HiBid platform credentials
5. Update all placeholder values in config files

## Performance Optimizations

- **Image Processing**: Concurrent processing with memory limits
- **Celery Configuration**: Optimized worker concurrency
- **Database Connections**: Connection pooling with `CONN_MAX_AGE`
- **Static Files**: WhiteNoise compression and caching
- **Task Timeout**: 1-hour hard limit with 55-minute soft timeout

## Integration APIs

- **Airtable**: Data source for inventory and sales
- **HiBid**: Auction platform integration
- **MinIO**: Image storage with public URL generation
- **n8n**: Webhook automation endpoints
- **RelayThat**: Marketing image generation

## Technical Highlights

### Celery Task Management
- Real-time progress tracking via Redis
- Task states: NOT_STARTED, IN_PROGRESS, COMPLETED, ERROR, WARNING
- Automatic task history cleanup (24-hour retention)
- Memory management for large dataset processing

### Browser Automation
- Async/await patterns with Playwright
- Screenshot capture on errors
- Configurable timeouts and retry logic
- Headless and headed mode support

### Image Processing Pipeline
- JPEG conversion with quality optimization
- Automatic resizing and orientation correction
- Progressive loading support
- EXIF metadata handling

## Documentation

- [CLAUDE.md](CLAUDE.md) - Development guide for AI assistants
- [knowledge.md](knowledge.md) - Comprehensive system documentation

## Contributing

This is a portfolio project and not actively maintained for public contributions. However, feel free to fork and adapt it for your own use.

## License

This project is provided as-is for demonstration purposes.

## Contact

For questions about this project or professional inquiries, please reach out via [GitHub](https://github.com/yourusername).

---

**Built with:** Django • Celery • Playwright • Redis • PostgreSQL • Docker

**Deployed on:** Heroku
