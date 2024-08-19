from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import CustomUser, Auction, Bid

class CustomUserAdmin(UserAdmin):
    model = CustomUser
    list_display = ['username', 'email', 'is_staff', 'is_active', 'is_standard_user']
    fieldsets = UserAdmin.fieldsets + (
        ('Custom Fields', {'fields': ('is_standard_user',)}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Custom Fields', {'fields': ('is_standard_user',)}),
    )

admin.site.register(CustomUser, CustomUserAdmin)
admin.site.register(Auction)
admin.site.register(Bid)