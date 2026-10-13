"""
Tool actions shared by the phone (Twilio) and browser (WebSocket) handlers.

Each action saves to the database and returns (saved_record_or_None, reply_text).
The reply only confirms the action when the save actually succeeded.
"""

from backend.database import db
from backend.models import TicketCreate
from backend.faq import get_faq_answer

DEFAULT_ROOM = "402"
SAVE_FAILED_REPLY = "Sorry, I couldn't log that. Let me have the front desk follow up."
UPDATE_FAILED_REPLY = "Sorry, I had trouble updating that. Let me have the front desk follow up."
UPDATABLE_FIELDS = ("room", "item", "quantity", "priority", "description")


async def create_ticket(args: dict) -> tuple[dict | None, str]:
    room = args.get("room") or DEFAULT_ROOM
    item = args.get("item") or ""
    quantity = args.get("quantity") or 1
    try:
        ticket_data = TicketCreate(
            room=str(room),
            request_type=args.get("request_type") or "housekeeping",
            item=item,
            quantity=quantity,
            priority=args.get("priority") or "medium",
            description=args.get("description") or "",
        )
        saved = await db.create_ticket(ticket_data)
    except Exception as e:
        print(f"❌ Could not create ticket from {args}: {e}")
        saved = None

    if not saved:
        return None, SAVE_FAILED_REPLY

    print(f"💾 Ticket saved: {saved}")
    what = f"{quantity} {item}" if item else "your request"
    return saved, f"Ticket created for {what} in room {room}. Is there anything else I can help you with?"


async def update_ticket(ticket_id: str, args: dict) -> tuple[dict | None, str]:
    updates = {k: args[k] for k in UPDATABLE_FIELDS if args.get(k) is not None}
    if not updates:
        return None, "What would you like me to change on that request?"

    try:
        updated = await db.update_ticket(ticket_id, updates)
    except Exception as e:
        print(f"❌ Could not update ticket {ticket_id}: {e}")
        updated = None

    if not updated:
        print(f"⚠️ Could not update ticket {ticket_id} with {updates}")
        return None, UPDATE_FAILED_REPLY

    print(f"💾 Ticket updated: {updated}")
    bits = ", ".join(f"{k} to {v}" for k, v in updates.items())
    return updated, f"Got it, I've updated the {bits}. Anything else?"


async def set_wakeup_call(args: dict) -> tuple[dict | None, str]:
    room = args.get("room") or DEFAULT_ROOM
    time = args.get("time")
    if not time:
        return None, "What time would you like your wake-up call?"

    try:
        saved = await db.create_ticket(TicketCreate(
            room=str(room),
            request_type="wakeup_call",
            item="wake-up call",
            quantity=1,
            priority="medium",
            description=f"Wake-up call at {time}",
        ))
    except Exception as e:
        print(f"❌ Could not save wake-up call {args}: {e}")
        saved = None

    if not saved:
        return None, SAVE_FAILED_REPLY

    print(f"💾 Wake-up call saved: {saved}")
    return saved, f"Wake-up call set for {time} in room {room}. Anything else?"


def answer_faq(args: dict) -> str:
    answer = get_faq_answer(args.get("topic") or "")
    if not answer or answer == "I don't have information on that.":
        answer = "I don't have that information. Would you like me to connect you with the front desk?"
    return answer + " Anything else?"
