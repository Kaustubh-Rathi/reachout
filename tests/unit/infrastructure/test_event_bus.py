"""Integration tests for domain events publishing, filtering, and subscription bus."""

from app.infrastructure.events.event_bus import EventBus
from app.ports.infrastructure import DomainEvent, EventPublisher


class TestEventsIntegration:
    def test_event_bus_pub_sub_and_filtering(self):
        bus = EventBus(max_buffer_size=50)
        assert isinstance(bus, EventPublisher)

        all_received = []
        started_received = []

        def general_listener(evt: DomainEvent):
            all_received.append(evt)

        def campaign_listener(evt: DomainEvent):
            started_received.append(evt)

        bus.subscribe(general_listener)
        bus.subscribe(campaign_listener, event_type="CampaignStarted")

        evt1 = DomainEvent(event_type="CampaignStarted", payload={"campaign_id": "cmp_1"})
        evt2 = DomainEvent(event_type="AttemptSent", payload={"attempt_id": "att_1"})

        bus.publish(evt1)
        bus.publish(evt2)

        assert len(all_received) == 2
        assert len(started_received) == 1
        assert started_received[0].payload["campaign_id"] == "cmp_1"

        # Check event history ring buffer (most recent first)
        history = bus.get_history(10)
        assert len(history) == 2
        assert history[0]["event_type"] == "AttemptSent"
        assert history[1]["event_type"] == "CampaignStarted"

        # Unsubscribe
        bus.unsubscribe(general_listener)
        bus.publish(DomainEvent(event_type="AttemptSent", payload={"attempt_id": "att_2"}))
        assert len(all_received) == 2  # Not increased
