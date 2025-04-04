# Auction Management System Knowledge

## Project Overview
- Django-based auction management system for managing liquidation auctions
- Handles auction creation, formatting, and management of unpaid transactions
- Uses Celery for background tasks
- Integrates with HiBid auction platform and Airtable for data management

## Key Components

### Warehouses
- System supports multiple warehouses (Maule and Sahara)
- Each warehouse has specific configurations and credentials
- Warehouse selection affects auction details like pickup location and terms

### Authentication
- Always use Maule Warehouse credentials for HiBid login regardless of selected warehouse
- Login credentials are stored in config.json

### Tasks
All major operations run as Celery tasks:
- Auction creation
- Auction formatting
- Void unpaid transactions
- Remove duplicates

### Image Processing
- Images are processed through MinIO for storage
- Image processing includes resizing, orientation fixing, and optimization
- Rate limiting is implemented for image uploads

### Data Flow
1. Data originates from Airtable
2. Gets processed and formatted
3. Uploaded to HiBid platform
4. Results stored in local database

### Remove Duplicates Process
- Helps manage duplicate items across auctions in Airtable
- Takes target MSRP as input to control volume
- Randomly selects items to avoid predictable patterns
- Updates 'Auctions' field in Airtable to track which items are in which auctions
- Groups items by product name and updates up to half of each group
- Stops when target MSRP is reached
- Progress tracking in 10% increments for large batches

## Important Rules

### File Handling
- Use MinIO for image storage instead of local filesystem
- Always clean up temporary files in finally blocks
- Handle file operations in chunks for memory efficiency

### Error Handling
- Always implement retries for external service calls
- Log errors with full tracebacks
- Update task status for frontend feedback

### Task Status Updates
- Use RedisTaskStatus for real-time progress updates
- Include both state and descriptive messages
- States: ["NOT_STARTED", "IN_PROGRESS", "COMPLETE", "FAILED"]

### Configuration
- Use config_manager for all configuration access
- Config is warehouse-specific - always set active warehouse first
- Keep sensitive credentials in config.json

### Airtable Integration
- Use pyairtable for Airtable operations
- Handle rate limits through batching
- Validate auction numbers against database before processing
- Cache expensive Airtable queries when possible

## Common Patterns

### Browser Automation
- Use Playwright for web automation
- Always implement proper waits and error handling
- Take screenshots on errors for debugging

### CSV Processing
- Use pandas for large CSV operations
- Process in batches for memory efficiency
- Validate CSV content before saving/uploading

### Database Operations
- Use transactions for related operations
- Implement proper async/sync patterns with Django
- Cache expensive operations when possible

## Optimization Guidelines
- Use batch processing for bulk operations
- Implement caching for frequent operations
- Control memory usage with chunking
- Rate limit external API calls

## Testing
- Run tests before deploying changes
- Test with both warehouses
- Verify image processing works
- Check task status updates work

## Links
- [HiBid API Documentation](https://bid.702auctions.com/api/docs)
- [Airtable API Reference](https://airtable.com/api)