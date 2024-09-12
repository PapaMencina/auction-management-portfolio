from django.db import models
from django.contrib.auth.models import AbstractUser

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

class TaskProgress(models.Model):
    task_id = models.CharField(max_length=255, unique=True)
    progress = models.IntegerField(default=0)
    status = models.TextField()  # Changed from CharField to TextField
    error = models.TextField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Task {self.task_id}: {self.status} ({self.progress}%)"