import json
from backend.faq import FAQ_KB, get_faq_answer

# --- Tool Definitions for LLM ---
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "create_ticket",
            "description": "Create a NEW housekeeping or maintenance ticket for a fresh request.",
            "parameters": {
                "type": "object",
                "properties": {
                    "room": {"type": "string", "description": "Room number (e.g., '402')"},
                    "request_type": {
                        "type": "string", 
                        "enum": ["housekeeping", "maintenance"],
                        "description": "Type of request"
                    },
                    "item": {"type": "string", "description": "What the guest needs"},
                    "quantity": {"type": "integer", "description": "Number of items requested"},
                    "priority": {
                        "type": "string", 
                        "enum": ["low", "medium", "high"],
                        "description": "Urgency of the request"
                    },
                    "description": {"type": "string", "description": "Full description"},
                },
                "required": ["room", "request_type", "item", "priority", "description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_last_ticket",
            "description": (
                "Correct or add a detail to the ticket that was JUST created in this same call "
                "(e.g. the guest gives a room number they forgot, changes the quantity, or "
                "changes the item). Only include the fields that changed. Do NOT use this to "
                "create a brand new, unrelated request — use create_ticket for that."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "room": {"type": "string", "description": "Corrected room number, if changed"},
                    "item": {"type": "string", "description": "Corrected item, if changed"},
                    "quantity": {"type": "integer", "description": "Corrected quantity, if changed"},
                    "priority": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                        "description": "Corrected priority, if changed",
                    },
                    "description": {"type": "string", "description": "Corrected description, if changed"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "answer_faq",
            "description": "Look up a common hotel question from the knowledge base.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "enum": list(FAQ_KB.keys())},
                },
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_wakeup_call",
            "description": "Set a wake-up call for a guest at a specific time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "room": {"type": "string", "description": "Room number"},
                    "time": {"type": "string", "description": "Time (e.g., '7:00 AM')"},
                },
                "required": ["room", "time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "end_call",
            "description": (
                "Call this when the guest indicates they are finished and the call "
                "should end — e.g. they say no more requests, thank you, goodbye, "
                "that's all, I'm all set, etc. Do NOT call this if they are just "
                "pausing mid-request or answering a clarifying question."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "farewell": {
                        "type": "string",
                        "description": "A short, warm goodbye line to say to the guest before hanging up.",
                    }
                },
                "required": [],
            },
        },
    }
]

SYSTEM_PROMPT = """You are a friendly hotel voice concierge. The guest is in room 402.

Tools:
- create_ticket: for a brand NEW item request (towels, pillows, coffee) or reported issue (AC, TV, etc).
- update_last_ticket: use this INSTEAD of create_ticket when the guest is correcting or adding a
  missing detail to the request they JUST made a moment ago in this same call (e.g. they forgot to
  give their room number and now say "it's room 405", or they say "actually make that 5, not 3").
  Only pass the fields that changed.
- answer_faq: for questions about wifi, checkout, gym, breakfast, or wake-up calls.
- set_wakeup_call: for setting a wake-up call at a specific time.
- end_call: When the guest signals they have nothing further (e.g. 'no thanks', 'that's it', 'I'm good', 'bye'), call the end_call tool instead of just replying.

Rules:
- One message can contain multiple requests - call multiple tools if needed.
- If key info is missing, ask a short clarifying question instead of guessing.
- If the guest's next message is clearly a correction/addition to what they just asked for
  (not a new, separate request), call update_last_ticket rather than create_ticket, so we don't
  create a duplicate ticket.
- Keep spoken replies short and natural.
"""