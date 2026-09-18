from supabase import create_client, Client
from backend.config import config
from backend.models import Ticket, TicketCreate, WorkerCreate

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

    # ------------------------------------------------------------------
    # Tickets
    # ------------------------------------------------------------------

    def create_ticket(self, ticket: TicketCreate) -> dict:
        """Create a ticket in Supabase or in-memory, then try to auto-assign it
        to a free worker immediately."""
        payload = ticket.model_dump()
        payload["status"] = "open"  # explicit default — don't rely on this being
                                     # implicitly unset, or on a DB column default.

        if self.is_connected():
            try:
                response = self._client.table("tickets").insert(payload).execute()
                saved = response.data[0] if response.data else None
            except Exception as e:
                print(f"❌ Supabase error: {e}")
                saved = None
        else:
            # In-memory fallback
            ticket_data = dict(payload)
            ticket_data["id"] = str(hash(str(ticket_data)))
            ticket_data["created_at"] = "now()"
            ticket_data["assigned_worker_id"] = None
            ticket_data["assigned_worker_name"] = None
            if not hasattr(self, "_tickets"):
                self._tickets = []
            self._tickets.append(ticket_data)
            saved = ticket_data

        if saved and saved.get("id"):
            assigned = self.try_auto_assign(saved["id"])
            if assigned:
                saved = assigned

        return saved

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

    def get_ticket(self, ticket_id: str) -> dict | None:
        for t in self.get_tickets():
            if t.get("id") == ticket_id:
                return t
        return None

    def complete_ticket(self, ticket_id: str) -> dict | None:
        """Mark a ticket completed, free its worker (if any), and hand that
        worker the oldest still-open ticket, if one exists."""
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            return None

        updated = self.update_ticket(ticket_id, {"status": "completed"})

        worker_id = ticket.get("assigned_worker_id")
        if worker_id:
            self.update_worker(worker_id, {"status": "available"})
            self.assign_next_ticket_to(worker_id)

        return updated

    # ------------------------------------------------------------------
    # Workers
    # ------------------------------------------------------------------

    def create_worker(self, worker: WorkerCreate) -> dict:
        if self.is_connected():
            try:
                payload = worker.model_dump()
                payload["status"] = "available"
                response = self._client.table("workers").insert(payload).execute()
                return response.data[0] if response.data else None
            except Exception as e:
                print(f"❌ Supabase error: {e}")
                return None
        else:
            data = worker.model_dump()
            if not hasattr(self, "_workers"):
                self._workers = []
            data["id"] = str(hash(worker.name + str(len(self._workers))))
            data["status"] = "available"
            data["created_at"] = "now()"
            self._workers.append(data)
            return data

    def get_workers(self):
        if self.is_connected():
            try:
                response = self._client.table("workers").select("*").order(
                    "created_at"
                ).execute()
                return response.data
            except Exception as e:
                print(f"❌ Supabase error: {e}")
                return []
        else:
            return getattr(self, "_workers", [])

    def get_worker(self, worker_id: str) -> dict | None:
        for w in self.get_workers():
            if w.get("id") == worker_id:
                return w
        return None

    def update_worker(self, worker_id: str, updates: dict) -> dict | None:
        if self.is_connected():
            try:
                response = self._client.table("workers").update(
                    updates
                ).eq("id", worker_id).execute()
                return response.data[0] if response.data else None
            except Exception as e:
                print(f"❌ Supabase error: {e}")
                return None
        else:
            workers = getattr(self, "_workers", [])
            for w in workers:
                if w.get("id") == worker_id:
                    w.update(updates)
                    return w
            return None

    # ------------------------------------------------------------------
    # Assignment logic
    # ------------------------------------------------------------------

    def _find_available_worker(self) -> dict | None:
        for w in self.get_workers():
            if w.get("status") == "available":
                return w
        return None

    def try_auto_assign(self, ticket_id: str) -> dict | None:
        """Called right after a ticket is created. If a worker is free,
        assign them immediately and flip the ticket to in_progress.
        If nobody's free, leave the ticket open — returns None."""
        worker = self._find_available_worker()
        if not worker:
            return None

        updated = self.update_ticket(ticket_id, {
            "status": "in_progress",
            "assigned_worker_id": worker["id"],
            "assigned_worker_name": worker.get("name"),
        })
        if updated:
            self.update_worker(worker["id"], {"status": "busy"})
        return updated

    def assign_next_ticket_to(self, worker_id: str) -> dict | None:
        """Find the oldest still-open ticket and hand it to this (now-free) worker."""
        open_tickets = [t for t in self.get_tickets() if t.get("status") == "open"]
        if not open_tickets:
            return None

        # Oldest first. created_at strings sort correctly for ISO timestamps;
        # in-memory fallback tickets all share "now()" so order is effectively FIFO by list order.
        open_tickets.sort(key=lambda t: t.get("created_at") or "")
        oldest = open_tickets[0]

        worker = self.get_worker(worker_id)
        updated = self.update_ticket(oldest["id"], {
            "status": "in_progress",
            "assigned_worker_id": worker_id,
            "assigned_worker_name": worker.get("name") if worker else None,
        })
        if updated:
            self.update_worker(worker_id, {"status": "busy"})
        return updated

# Singleton instance
db = Database()