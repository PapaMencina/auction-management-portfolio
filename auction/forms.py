from django import forms
from .models import Auction

class AuctionForm(forms.ModelForm):
    class Meta:
        model = Auction
        fields = ['title', 'description', 'start_time', 'end_time']
