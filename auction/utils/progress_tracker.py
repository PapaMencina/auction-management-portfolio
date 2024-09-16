import logging

logger = logging.getLogger(__name__)

class SharedEvents:
    def __init__(self):
        self.events = []

    def add_event(self, title, event_id, ending_date, timestamp):
        self.events.append({
            "title": title,
            "event_id": event_id,
            "ending_date": str(ending_date),
            "timestamp": timestamp
        })
        logger.info(f"Event added: {title}, ID: {event_id}, Ending Date: {ending_date}, Timestamp: {timestamp}")

shared_events = SharedEvents()