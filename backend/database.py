from supabase import create_client, Client
from backend.config import config
from backend.models import Ticket, TicketCreate

class Database:
    _instance = None
    _client: Client = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            if config.SUPABASE_URL and config.SUPABASE_KEY:
                cls._instance._client = create_client(
                    config.SUPABASE_URL, 
                    config.SUPABASE_PUBLISHABLE_KEY
                )
                print("✅ Supabase connected")
            else:
                print("⚠️ Supabase not configured - using in-memory storage")
                cls._instance._client = None
        return cls._instance
    
    @property
    def client(self):
        return self._client
    
    def is_connected(self):
        return self._client is not None
    
    def create_ticket(self, ticket: TicketCreate) -> dict:
        """Create a ticket in Supabase or in-memory"""
        if self.is_connected():
            try:
                response = self._client.table("tickets").insert(
                    ticket.model_dump()
                ).execute()
                return response.data[0] if response.data else None
            except Exception as e:
                print(f"❌ Supabase error: {e}")
                return None
        else:
            # In-memory fallback
            ticket_data = ticket.model_dump()
            ticket_data["id"] = str(hash(str(ticket_data)))
            ticket_data["created_at"] = "now()"
            if not hasattr(self, "_tickets"):
                self._tickets = []
            self._tickets.append(ticket_data)
            return ticket_data

    def update_ticket(self, ticket_id: str, updates: dict) -> dict | None:
        """Update an existing ticket by id, in Supabase or in-memory.

        `updates` should only contain the fields that changed
        (e.g. {"room": "405"} or {"quantity": 5, "item": "pillows"}).
        Returns the updated ticket dict, or None if the ticket wasn't found.
        """
        if not updates:
            return None

        if self.is_connected():
            try:
                response = self._client.table("tickets").update(
                    updates
                ).eq("id", ticket_id).execute()
                return response.data[0] if response.data else None
            except Exception as e:
                print(f"❌ Supabase error: {e}")
                return None
        else:
            tickets = getattr(self, "_tickets", [])
            for t in tickets:
                if t.get("id") == ticket_id:
                    t.update(updates)
                    return t
            return None
    
    def get_tickets(self, limit=100):
        """Get all tickets"""
        if self.is_connected():
            try:
                response = self._client.table("tickets").select("*").order(
                    "created_at", desc=True
                ).limit(limit).execute()
                return response.data
            except Exception as e:
                print(f"❌ Supabase error: {e}")
                return []
        else:
            return getattr(self, "_tickets", [])

# Singleton instance
db = Database()