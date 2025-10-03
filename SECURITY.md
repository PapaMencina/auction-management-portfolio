# Security Notice

## Repository Sanitization

⚠️ **This repository has been sanitized for public sharing and portfolio purposes.**

### What Was Removed

This branch (`secure-for-sharing`) has had all sensitive information removed, including:

1. **Database File** (`db.sqlite3`)
   - 14MB SQLite database containing production data
   - Completely removed from git history

2. **Environment Variables** (`.env`)
   - Django secret keys
   - Production database URLs
   - Redis connection strings
   - All sensitive configuration

3. **API Credentials** (`auction/utils/config.json`)
   - Airtable API keys and base IDs
   - MinIO access keys and secrets
   - HiBid platform credentials
   - RelayThat authentication
   - Email credentials

### History Rewrite

The git history for this branch has been completely rewritten using `git filter-branch` to remove all traces of:
- `.env` file and its contents
- `auction/utils/config.json` and its contents
- `db.sqlite3` database file

**Note:** The `master` branch and other branches retain the original history and should NEVER be made public.

## Configuration Setup

To run this application, you'll need to:

### 1. Create Environment File

```bash
cp .env.example .env
```

Then edit `.env` with your actual values:

```bash
# Generate a new Django secret key
python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'

# Add to .env:
DJANGO_SECRET_KEY=your-generated-key-here
DJANGO_DEBUG=True  # Only for development!

# Add database URL for production
DATABASE_URL=postgresql://user:pass@host:port/dbname

# Add Redis URL
REDIS_URL=redis://localhost:6379/0
```

### 2. Create Configuration File

```bash
cp auction/utils/config.json.example auction/utils/config.json
```

Then update `config.json` with your actual:
- Airtable API keys and base IDs
- MinIO/S3 credentials
- HiBid platform credentials
- RelayThat API credentials
- Email configuration

### 3. Set Up Required Services

Before running the application, ensure you have:

#### MinIO (Object Storage)
- Set up a MinIO server or AWS S3
- Create a bucket for auction images
- Configure access keys

#### Airtable
- Create Airtable bases for inventory and sales
- Generate a personal access token
- Note all base IDs, table IDs, and view IDs

#### Redis
- Install and run Redis server
- Or use a cloud Redis service (e.g., Heroku Redis, Redis Labs)

#### HiBid Account
- Sign up for HiBid auction platform
- Obtain account credentials

## Security Best Practices

### Never Commit Sensitive Data

The `.gitignore` file is configured to prevent committing:
- `.env` files
- `config.json`
- `db.sqlite3` database
- Any `.pem`, `.key`, or `.cert` files

### Environment-Specific Configuration

Use environment variables for production:
- `DJANGO_SECRET_KEY`
- `DATABASE_URL`
- `REDIS_URL`
- All API keys and credentials

### Credential Rotation

If you suspect credentials have been compromised:
1. Immediately rotate all API keys
2. Change all passwords
3. Update environment variables
4. Redeploy application

## Production Deployment

### Heroku Configuration

Set environment variables via Heroku CLI or dashboard:

```bash
heroku config:set DJANGO_SECRET_KEY=your-secret-key
heroku config:set DATABASE_URL=your-database-url
heroku config:set REDIS_URL=your-redis-url
```

### Security Headers

The application is configured with:
- HTTPS enforcement (production)
- HSTS with preload
- Secure cookies
- CSRF protection
- Secure referrer policy

## Reporting Security Issues

This is a portfolio project. For questions or concerns, please contact via GitHub.

## Additional Notes

- **Public Website References**: The code contains references to "702auctions.com" and "bid.702auctions.com" which are public-facing URLs and not sensitive
- **Business Name**: References to "702Auctions" are the public business name
- **Example Data**: All configuration files contain only example/placeholder data

---

**Last Updated:** October 2025
**Sanitization Method:** git filter-branch + manual credential removal
**Branch:** secure-for-sharing (safe for public portfolio use)
