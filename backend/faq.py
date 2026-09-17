# Grounded FAQ Knowledge Base
FAQ_KB = {
    "wifi": "The Wi-Fi password is 'HotelGuest2024'.",
    "checkout": "Checkout is at 11 AM. Late checkout is available until 2 PM.",
    "gym": "The gym is open 24/7 on the second floor.",
    "breakfast": "Breakfast is served from 7-10 AM in the lobby restaurant.",
    "wakeup": "I can set a wake-up call for you — what time would you like it?",
    "parking": "Parking is available in the garage for $20 per night.",
    "pool": "The pool is open from 6 AM to 10 PM.",
    "restaurant": "Our restaurant serves dinner from 6-10 PM.",
    "room_service": "Room service is available from 6 AM to 10 PM.",
}

def get_faq_answer(topic: str) -> str:
    """Get answer from knowledge base"""
    return FAQ_KB.get(topic, "I don't have information on that.")

def get_all_topics():
    """Get all FAQ topics"""
    return list(FAQ_KB.keys())
