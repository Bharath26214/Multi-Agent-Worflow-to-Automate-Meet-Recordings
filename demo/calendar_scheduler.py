from ics import Attendee, Calendar, Event

def create_ics():
    c = Calendar()

    e = Event()
    e.name = "Follow-up meeting"
    e.begin = "2026-04-22 10:00:00"
    e.end = "2026-04-22 11:00:00"
    e.description = "Discuss API progress"

    # Use ICS Attendee objects (dicts are not valid here).
    e.attendees = {
        Attendee(email="john@company.com", common_name="John"),
        Attendee(email="x@company.com", common_name="X"),
    }

    c.events.add(e)

    with open("demo/meeting.ics", "w", encoding="utf-8") as f:
        f.write(c.serialize())

create_ics()