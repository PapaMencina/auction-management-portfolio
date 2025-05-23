from django.core.management.base import BaseCommand
from django.utils import timezone
from auction.models import Event

class Command(BaseCommand):
    help = 'Check events in the database and debug filtering issues'

    def add_arguments(self, parser):
        parser.add_argument(
            '--warehouse',
            type=str,
            help='Filter by warehouse name',
        )

    def handle(self, *args, **options):
        today = timezone.now().date()
        warehouse = options.get('warehouse')
        
        # Get all events
        if warehouse:
            events = Event.objects.filter(warehouse=warehouse)
            self.stdout.write(f"\nEvents for warehouse '{warehouse}':")
        else:
            events = Event.objects.all()
            self.stdout.write("\nAll events in database:")
        
        self.stdout.write(f"Total count: {events.count()}")
        self.stdout.write(f"Current date: {today}\n")
        
        # Show each event
        for event in events.order_by('-timestamp'):
            status = "ACTIVE" if event.ending_date >= today else "ENDED"
            days_until = (event.ending_date - today).days
            
            self.stdout.write(
                f"ID: {event.event_id} | "
                f"Title: {event.title} | "
                f"Warehouse: '{event.warehouse}' | "
                f"Ending: {event.ending_date} | "
                f"Status: {status} | "
                f"Days: {days_until}"
            )
        
        # Show unique warehouses
        self.stdout.write("\n\nUnique warehouses in database:")
        warehouses = Event.objects.values_list('warehouse', flat=True).distinct()
        for wh in warehouses:
            count = Event.objects.filter(warehouse=wh).count()
            self.stdout.write(f"  '{wh}' ({count} events)")
        
        # Show future events that should appear in dropdowns
        self.stdout.write("\n\nFuture events (should appear in dropdowns):")
        future_events = Event.objects.filter(ending_date__gte=today)
        for event in future_events:
            self.stdout.write(
                f"  {event.event_id} - {event.title} - Warehouse: '{event.warehouse}' - Ends: {event.ending_date}"
            ) 