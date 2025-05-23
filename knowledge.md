# Auction Management System Knowledge Base

## Project Overview

The Auction Management System is a comprehensive Django-based web application designed to automate and manage auction operations for multiple warehouses. The system integrates with various external services to provide end-to-end auction management from data ingestion to final auction upload.

**Primary Purpose**: Automate the complete auction workflow including data formatting, image processing, auction creation, transaction management, and platform integration.

## System Architecture

### Core Technology Stack
- **Backend**: Django 3.2.23 (Python web framework)
- **Task Queue**: Celery 5.4.0 with Redis backend
- **Database**: PostgreSQL (production) / SQLite (development)
- **Object Storage**: MinIO for image storage and management
- **Web Automation**: Playwright 1.47.0 for browser automation
- **Container Platform**: Docker with multi-service architecture
- **Deployment**: Heroku with container deployment via heroku.yml

### Infrastructure Components
- **Web Server**: Gunicorn with gthread workers (optimized for Heroku)
- **Worker Processes**: Dedicated Celery workers for background tasks
- **Caching/Message Broker**: Redis for task queues and caching
- **Static Files**: WhiteNoise for static file serving
- **Image Storage**: MinIO with S3-compatible API
- **Load Balancing**: Nginx configuration included

### Deployment Configurations
1. **Development**: Docker Compose with local Redis and MinIO
2. **Production**: Heroku with external Redis and MinIO services
3. **Worker Scaling**: Separate worker dynos with resource optimization

## Data Architecture

### Core Models

#### CustomUser
- Extends Django's AbstractUser
- `is_standard_user`: Boolean for user type classification
- Custom permission: `"can_use_show_browser"` for browser automation access
- Integrated with Django's authentication system

#### Event
- Central model representing auction events
- **Fields**:
  - `event_id`: Unique identifier (CharField, max_length=100)
  - `warehouse`: Warehouse association (CharField, max_length=100)
  - `title`: Event display name (CharField, max_length=200)
  - `start_date`: Event start date (DateField)
  - `ending_date`: Event end date (DateField)
  - `timestamp`: Creation timestamp (DateTimeField, auto_now_add=True)
- **Methods**:
  - `is_active()`: Checks if event is currently active based on ending_date
- **Relationships**: Related to VoidedTransaction, ImageMetadata, AuctionFormattedData, HiBidUpload

#### ImageMetadata
- Manages auction images with MinIO integration
- **Fields**:
  - `event`: ForeignKey to Event
  - `filename`: Original filename (CharField, max_length=255)
  - `is_primary`: Primary image designation (BooleanField)
  - `uploaded_at`: Upload timestamp (DateTimeField, auto_now_add=True)
  - `image`: MinIO URL (URLField, max_length=1000)
- **Features**: Primary image designation, URL-based storage

#### AuctionFormattedData
- Stores processed CSV data for auction uploads
- `event`: ForeignKey to Event
- `csv_data`: Formatted CSV content (TextField)
- `created_at`: Creation timestamp

#### HiBidUpload
- Tracks upload status to HiBid platform
- `event`: ForeignKey to Event
- `upload_date`: Upload timestamp
- `status`: Upload status tracking (CharField, default='pending')

#### VoidedTransaction
- Records voided transaction data
- `event`: ForeignKey to Event
- `csv_data`: Transaction data (TextField)
- `timestamp`: Creation timestamp

## Business Logic & Workflows

### Warehouse Management
The system supports multiple warehouse operations with distinct configurations:

#### Supported Warehouses
1. **Maule Warehouse**
   - Lot prefix: 'M'
   - Region ID: "88850842"
   - Dedicated Airtable bases and credentials
   
2. **Sahara Warehouse**
   - Lot prefix: 'S'
   - Region ID: "88850843" 
   - Separate configuration and credentials

#### Warehouse Configuration Structure
```json
{
  "warehouses": {
    "Warehouse Name": {
      "airtable_sales_base_id": "...",
      "airtable_inventory_base_id": "...",
      "airtable_inventory_table_id": "...",
      "airtable_cancels_table_id": "...",
      "airtable_api_key": "...",
      "airtable_send_to_auction_view_id": "...",
      "airtable_remove_dups_view": "...",
      "bid_username": "...",
      "bid_password": "...",
      "hibid_user_name": "...",
      "hibid_password": "...",
      "relaythat_url": "..."
    }
  }
}
```

### Core Task Workflows

#### 1. Auction Formatter Task (`auction_formatter_task`)
**Purpose**: Processes Airtable data and prepares CSV files for HiBid upload

**Process Flow**:
1. **Data Retrieval**: Fetches records from Airtable using warehouse-specific views
2. **Image Processing Pipeline**:
   - Downloads images from Airtable URLs
   - Processes images (resize, optimize, format conversion)
   - Uploads to MinIO with unique filenames
   - Generates public URLs for HiBid consumption
3. **Data Transformation**:
   - Converts Airtable records to HiBid-compatible format
   - Applies category mapping (85+ category mappings)
   - Generates lot numbers with warehouse prefixes
   - Creates formatted descriptions with HTML
4. **CSV Generation**: Creates properly formatted CSV for HiBid import
5. **Database Storage**: Saves formatted data in AuctionFormattedData model

**Performance Optimizations**:
- Concurrent image processing (configurable limits)
- Memory management with cleanup cycles
- Rate limiting for API calls
- Caching with TTL for Airtable records

#### 2. Auction Creation Task (`create_auction_task`)
**Purpose**: Creates new auction events on HiBid platform

**Process Flow**:
1. **Input Validation**: Validates auction title, dates, and warehouse selection
2. **Browser Automation**: Uses Playwright to navigate HiBid interface
3. **Event Creation**: 
   - Logs into HiBid platform
   - Fills auction creation form
   - Sets dates, times, and warehouse-specific settings
4. **Database Recording**: Creates Event record in local database
5. **Notification**: Sends completion notifications

#### 3. Void Unpaid Task (`void_unpaid_task`)
**Purpose**: Processes unpaid transactions and voids them on HiBid

**Process Flow**:
1. **Data Validation**: Validates event exists and has ended
2. **Transaction Retrieval**: Gets unpaid transaction data
3. **Browser Automation**: Navigates HiBid admin interface
4. **Void Processing**: Processes void operations with upload choices
5. **Record Keeping**: Stores voided transaction data

#### 4. Remove Duplicates Task (`remove_duplicates_task`)
**Purpose**: Identifies and removes duplicate items in Airtable

**Process Flow**:
1. **Auction Validation**: Verifies auction number exists in database
2. **Duplicate Detection**: Identifies duplicates based on MSRP targeting
3. **Batch Processing**: Removes duplicates via Airtable API
4. **Progress Tracking**: Real-time progress updates via Redis

### Integration Architecture

#### Airtable Integration
- **API**: pyairtable 2.3.3 for Python API access
- **Rate Limiting**: Implemented to respect Airtable API limits
- **Batch Operations**: Optimized for large dataset processing
- **View-Based Access**: Uses specific views for different operations
- **Error Handling**: Comprehensive error handling with retries

#### HiBid Platform Integration
- **Authentication**: Multi-warehouse credential management
- **Browser Automation**: Playwright-based automation for web interface
- **CSV Upload**: Direct file upload automation
- **Session Management**: Persistent sessions with timeout handling
- **Screenshot Capture**: Error documentation and debugging

#### MinIO Object Storage
- **Configuration**: S3-compatible API with custom endpoint
- **Image Pipeline**: Automated image processing and upload
- **Public URLs**: Generates publicly accessible image URLs
- **Bucket Management**: Automated bucket creation and policy setting
- **Error Handling**: Comprehensive upload and access error handling

#### External Service Integrations
1. **n8n Workflow**: HiBid upload automation via webhook endpoints
2. **RelayThat**: Marketing image generation integration
3. **Email Notifications**: SMTP-based notification system

## Task Management System

### Redis-Based Task Status
- **States**: NOT_STARTED, IN_PROGRESS, COMPLETED, ERROR, WARNING
- **Progress Tracking**: Real-time progress updates with percentage completion
- **History Management**: 24-hour task history retention
- **Stage Tracking**: Detailed stage and substage information

### Celery Configuration
- **Broker**: Redis with SSL support for production
- **Result Backend**: Redis-based result storage
- **Serialization**: JSON serialization for all tasks
- **Time Zone**: America/Los_Angeles
- **Connection Retry**: Automatic connection retry on startup

### Error Handling & Monitoring
- **Comprehensive Logging**: Structured logging throughout the application
- **Error Capture**: Screenshot capture for browser automation errors
- **Progress Callbacks**: Real-time progress updates via GUI callbacks
- **Memory Management**: Proactive memory monitoring and cleanup

## User Interface

### Web Application Structure
- **Authentication**: Required for all auction operations
- **Dashboard**: Overview of active/completed auctions with statistics
- **Warehouse Selection**: Dynamic warehouse switching
- **Task Management**: Real-time task status monitoring
- **File Downloads**: Direct CSV file download functionality

### Available Views
1. **Home Dashboard** (`/auction/`): Main dashboard with statistics
2. **Create Auction** (`/auction/create-auction/`): New auction creation
3. **Format Auction** (`/auction/format-auction/`): Data processing interface
4. **Void Unpaid** (`/auction/void-unpaid/`): Transaction management
5. **Remove Duplicates** (`/auction/remove-duplicates/`): Duplicate management
6. **Upload to HiBid** (`/auction/upload-to-hibid/`): Final upload interface

### Real-Time Features
- **AJAX-Based**: Asynchronous task monitoring
- **Progress Bars**: Visual progress indication
- **Status Updates**: Real-time status messages
- **Error Display**: Comprehensive error reporting

## Security & Configuration

### Authentication & Authorization
- **Custom User Model**: Extended Django authentication
- **Permission-Based Access**: Granular permission control
- **Session Management**: Secure session handling with timeouts
- **CSRF Protection**: Full CSRF protection implementation

### Security Measures
- **Environment Variables**: Sensitive configuration via environment variables
- **SSL/TLS**: HTTPS enforcement in production
- **Secure Headers**: HSTS, referrer policy, and other security headers
- **Database Security**: Connection pooling and SSL requirements

### Configuration Management
- **Environment-Based**: Different settings for development/production
- **Secret Management**: Secure credential storage
- **Warehouse Configs**: Centralized warehouse configuration management
- **Feature Flags**: Conditional feature enablement

## Performance & Optimization

### Resource Management
- **Memory Optimization**: Proactive memory management for large datasets
- **Concurrent Processing**: Configurable concurrency limits
- **Rate Limiting**: API rate limiting to prevent service overload
- **Caching**: TTL-based caching for expensive operations

### Deployment Optimizations
- **Heroku Optimization**: Optimized for Heroku dyno resources
- **Worker Scaling**: Dedicated worker processes for background tasks
- **Static File Handling**: WhiteNoise for efficient static file serving
- **Database Connections**: Optimized connection pooling

### Image Processing Pipeline
- **Format Optimization**: JPEG conversion with quality optimization
- **Size Management**: Automatic resizing for performance
- **Orientation Handling**: EXIF-based orientation correction
- **Progressive Loading**: Progressive JPEG for better user experience

## Development & Deployment

### Development Environment
- **Docker Compose**: Complete local development stack
- **Hot Reloading**: Development server with auto-reload
- **Local Services**: Local Redis and MinIO for development
- **Debug Mode**: Comprehensive debugging and logging

### Production Deployment
- **Heroku Platform**: Container-based deployment
- **Multi-Process**: Separate web and worker processes
- **Auto-Scaling**: Heroku auto-scaling capabilities
- **Health Monitoring**: Application health monitoring

### CI/CD Pipeline
- **Migration Handling**: Automatic database migrations
- **Static File Collection**: Automated static file management
- **Environment Configuration**: Environment-specific configurations
- **Dependency Management**: Locked dependency versions

## Troubleshooting & Maintenance

### Common Issues
1. **Memory Limits**: Image processing memory management
2. **Rate Limiting**: Airtable API rate limit handling
3. **Browser Automation**: Playwright timeout and error handling
4. **File Upload**: Large file upload timeout management

### Monitoring & Logging
- **Structured Logging**: Comprehensive application logging
- **Error Tracking**: Detailed error tracking and reporting
- **Performance Monitoring**: Task performance and duration tracking
- **Resource Monitoring**: Memory and processing resource monitoring

### Maintenance Tasks
- **Database Cleanup**: Regular cleanup of old records
- **Image Management**: MinIO storage management
- **Log Rotation**: Application log management
- **Dependency Updates**: Regular security and feature updates

## API Endpoints

### Internal APIs
- `/auction/get-warehouse-events/`: Warehouse-specific event filtering
- `/auction/check-task-status/<task_id>/`: Real-time task status
- `/auction/download-csv/<auction_id>/`: Processed CSV download

### External Integrations
- **n8n Webhook**: `https://n8n.702market.com/webhook/2e1ca1fa-9078-4c82-bb38-3650b38fea20`
- **HiBid APIs**: Direct integration with HiBid platform APIs
- **Airtable APIs**: Complete CRUD operations with Airtable

## Technical Specifications

### Dependencies (Key Versions)
- Django==3.2.23
- Celery==5.4.0
- Playwright==1.47.0
- pandas==2.2.3
- minio==7.2.15
- pyairtable==2.3.3
- redis==5.1.1
- gunicorn==23.0.0

### Database Schema
- **PostgreSQL**: Production database with full ACID compliance
- **Migrations**: Comprehensive Django migration system
- **Indexing**: Optimized database indexing for performance
- **Backup Strategy**: Automated backup and recovery procedures

This knowledge base provides comprehensive documentation for understanding, maintaining, and extending the Auction Management System. It serves as both technical reference and architectural guide for developers and system administrators.