# Auction Management System Knowledge Base

## System Architecture

### Core Technologies
- Django 3.2.23 (Python web framework)
- Celery 5.4.0 (Task queue)
- Redis (Task status tracking and caching)
- MinIO (Object storage for images)
- Playwright (Browser automation)
- Airtable (Data source)
- HiBid (Auction platform integration)

### Infrastructure
- Docker-based deployment (Dockerfile and Dockerfile.worker)
- Heroku deployment support (heroku.yml)
- PostgreSQL database (via dj-database-url)
- Gunicorn web server
- Celery workers for background tasks

## Data Models

### Core Models
1. **CustomUser**
   - Extends Django's AbstractUser
   - Supports standard user permissions
   - Special permission: "can_use_show_browser"

2. **Event**
   - Represents an auction event
   - Fields: event_id, warehouse, title, dates
   - Tracks active status
   - Related to: VoidedTransaction, ImageMetadata, AuctionFormattedData, HiBidUpload

3. **ImageMetadata**
   - Manages auction images
   - Supports primary image designation
   - Stores URLs (MinIO integration)
   - Tracks upload timestamps

4. **AuctionFormattedData**
   - Stores formatted CSV data for events
   - Used for data transformation and upload

5. **HiBidUpload**
   - Tracks upload status to HiBid platform
   - Maintains upload history

## Task System

### Core Tasks
1. **Auction Formatter Task**
   - Formats auction data for HiBid
   - Supports warehouse-specific configurations
   - Implements progress tracking
   - Handles starting price calculations

2. **Auction Creation Task**
   - Creates new auctions on HiBid
   - Validates date formats
   - Supports async operation
   - Configures warehouse-specific settings

3. **Void Unpaid Task**
   - Processes unpaid transactions
   - Uses Playwright for browser automation
   - Supports different upload choices
   - Warehouse-specific processing

4. **Remove Duplicates Task**
   - Manages duplicate items in Airtable
   - Validates auction numbers
   - Implements target MSRP control
   - Progress tracking with status updates

## Integration Points

### HiBid Integration
- Browser automation via Playwright
- Supports multiple warehouse configurations
- Handles authentication and session management
- Implements retry mechanisms for reliability

### Airtable Integration
- Uses pyairtable for API access
- Implements rate limiting
- Supports batch operations
- Validates auction numbers against database

### Image Processing
- MinIO for object storage
- Image optimization and resizing
- Primary image designation
- URL-based storage system

## Configuration Management

### Warehouse Configuration
- Supports multiple warehouses (Maule, Sahara)
- Warehouse-specific settings
- Credential management
- Configuration validation

### Task Status System
- Redis-based status tracking
- States: NOT_STARTED, IN_PROGRESS, COMPLETED, ERROR, WARNING
- 24-hour history retention
- Detailed progress tracking
- Stage and substage information

## Security Considerations

### Authentication
- Custom user model
- Permission-based access control
- Secure credential storage
- Session management

### Data Protection
- Secure file handling
- Temporary file cleanup
- Rate limiting implementation
- Error logging and monitoring

## Development Guidelines

### Code Organization
- Modular task structure
- Clear separation of concerns
- Consistent error handling
- Comprehensive logging

### Best Practices
1. **Task Implementation**
   - Always include progress tracking
   - Implement proper error handling
   - Use appropriate retry mechanisms
   - Clean up resources in finally blocks

2. **Data Processing**
   - Use batch processing for large datasets
   - Implement proper validation
   - Handle memory efficiently
   - Cache expensive operations

3. **Integration**
   - Validate external service responses
   - Implement proper timeouts
   - Handle rate limits gracefully
   - Maintain proper logging

## Testing and Deployment

### Testing Requirements
- Unit tests for core functionality
- Integration tests for external services
- Warehouse-specific testing
- Image processing validation

### Deployment Process
- Docker containerization
- Heroku deployment support
- Front-end assets collected via `collectstatic` for Heroku compatibility
- Environment variable management
- Database migration handling

## External Resources
- [HiBid API Documentation](https://bid.702auctions.com/api/docs)
- [Airtable API Reference](https://airtable.com/api)
- [Django Documentation](https://docs.djangoproject.com/en/3.2/)
- [Celery Documentation](https://docs.celeryq.dev/en/stable/)