from django.db import models
from django.contrib.auth.models import AbstractUser
from django.conf import settings

class CustomUser(AbstractUser):
    is_standard_user = models.BooleanField(default=False)

    class Meta:
        permissions = [
            ("can_use_show_browser", "Can use show browser option"),
        ]

class Auction(models.Model):
    title = models.CharField(max_length=200)
    description = models.TextField()
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    creator = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='created_auctions')

class Bid(models.Model):
    auction = models.ForeignKey(Auction, on_delete=models.CASCADE)
    bidder = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='bids')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    paid = models.BooleanField(default=False)
    timestamp = models.DateTimeField(auto_now_add=True)

class Event(models.Model):
    event_id = models.CharField(max_length=100, unique=True)
    warehouse = models.CharField(max_length=100)
    title = models.CharField(max_length=200)
    start_date = models.DateField()
    ending_date = models.DateField()
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} ({self.event_id})"

class VoidedTransaction(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='voided_transactions')
    csv_data = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Voided Transaction for Event {self.event.event_id}"
    
# In models.py
class ImageMetadata(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='images')
    filename = models.CharField(max_length=255)
    is_primary = models.BooleanField(default=False)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    image = models.ImageField(upload_to='auction_images/', null=True, blank=True)  # Allow null temporarily

    def __str__(self):
        return f"{self.filename} for event {self.event.event_id}"
    
class AuctionFormattedData(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='formatted_data')
    csv_data = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Formatted Data for Event {self.event.event_id}"
    
class HiBidUpload(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='hibid_uploads')
    upload_date = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=50, default='pending')
    lot_count = models.IntegerField(default=0)
    auction_id = models.CharField(max_length=100)
    ending_date = models.DateTimeField()

    def __str__(self):
        return f"HiBid Upload for Event {self.event.event_id} on {self.upload_date}"