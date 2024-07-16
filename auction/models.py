from django.db import models

class Auction(models.Model):
    title = models.CharField(max_length=200)
    description = models.TextField()
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()

class Bid(models.Model):
    auction = models.ForeignKey(Auction, on_delete=models.CASCADE)
    bidder = models.CharField(max_length=100)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    paid = models.BooleanField(default=False)
