import asyncio
import threading
import uuid
from datetime import datetime, timezone
from supabase import create_client, Client
from backend.config import config
from backend.models import TicketCreate, WorkerCreate


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DatabaseUnavailable(Exception):
    """Supabase couldn't be read, so "no rows" would be a lie."""


# Statuses in which a ticket is actually occupying its assigned worker
ACTIVE_STATUSES = ("assigned", "in_progress")


class Database:
    """Supabase-backed store with an in-memory fallback.

    The supabase client is synchronous, so the public methods are async wrappers
    that run the sync implementations (the `_`-prefixed methods) in a worker
    thread via asyncio.to_thread, keeping the event loop (and live calls) responsive.
    """

    _instance = None
    _client: Client = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            if config.SUPABASE_URL and config.SUPABASE_KEY:
                cls._instance._client = create_client(
                    config.SUPABASE_URL,
                    config.SUPABASE_KEY
                )
                print("✅ Supabase connected")
            else:
                print("⚠️ Supabase not configured - using in-memory storage")
                cls._instance._client = None
            # Serialises the multi-step assign/complete flows (and in-memory writes)
            # across the worker threads that asyncio.to_thread runs us on
            cls._instance._lock = threading.RLock()
            cls._instance._tickets = []
            cls._instance._workers = []
        return cls._instance
    
    @property
    def client(self):
        return self._client
    
    def is_connected(self):
        return self._client is not None

    # ------------------------------------------------------------------
    # Async public API
    # ------------------------------------------------------------------

    async def create_ticket(self, ticket: TicketCreate) -> dict | None:
        return await asyncio.to_thread(self._create_ticket, ticket)

    async def update_ticket(self, ticket_id: str, updates: dict) -> dict | None:
        return await asyncio.to_thread(self._update_ticket, ticket_id, updates)

    async def get_tickets(self, limit=100) -> list:
        return await asyncio.to_thread(self._get_tickets, limit)

    async def get_ticket(self, ticket_id: str) -> dict | None:
        return await asyncio.to_thread(self._get_ticket, ticket_id)

    async def complete_ticket(self, ticket_id: str) -> dict | None:
        return await asyncio.to_thread(self._complete_ticket, ticket_id)

    async def set_ticket_status(self, ticket_id: str, status: str) -> dict | None:
        return await asyncio.to_thread(self._set_ticket_status, ticket_id, status)

    async def create_worker(self, worker: WorkerCreate) -> dict | None:
        return await asyncio.to_thread(self._create_worker, worker)

    async def get_workers(self) -> list:
        return await asyncio.to_thread(self._get_workers)

    # ------------------------------------------------------------------
    # Tickets
    # ------------------------------------------------------------------

    def _create_ticket(self, ticket: TicketCreate) -> dict | None:
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
            ticket_data["id"] = str(uuid.uuid4())
            ticket_data["created_at"] = _now_iso()
            ticket_data["assigned_worker_id"] = None
            ticket_data["assigned_worker_name"] = None
            with self._lock:
                self._tickets.append(ticket_data)
            saved = dict(ticket_data)

        if saved and saved.get("id"):
            try:
                with self._lock:
                    assigned = self._try_auto_assign(saved["id"])
            except DatabaseUnavailable:
                # The ticket itself was saved; it just stays open for now
                assigned = None
            if assigned:
                saved = assigned

        return saved

    def _update_ticket(self, ticket_id: str, updates: dict) -> dict | None:
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
            with self._lock:
                for t in self._tickets:
                    if t.get("id") == ticket_id:
                        t.update(updates)
                        return dict(t)
            return None
    
    def _get_tickets(self, limit=100):
        """Get all tickets"""
        if self.is_connected():
            try:
                response = self._client.table("tickets").select("*").order(
                    "created_at", desc=True
                ).limit(limit).execute()
                return response.data
            except Exception as e:
                print(f"❌ Supabase error: {e}")
                raise DatabaseUnavailable(str(e)) from e
        else:
            # Copies, so callers never hold dicts another thread is mutating.
            # Newest first, matching the Supabase query above.
            with self._lock:
                return [dict(t) for t in reversed(self._tickets)][:limit]

    def _get_ticket(self, ticket_id: str) -> dict | None:
        for t in self._get_tickets():
            if t.get("id") == ticket_id:
                return t
        return None

    def _complete_ticket(self, ticket_id: str) -> dict | None:
        """Mark a ticket completed, free its worker (if any), and hand that
        worker the oldest still-open ticket, if one exists."""
        return self._set_ticket_status(ticket_id, "completed")

    def _set_ticket_status(self, ticket_id: str, status: str) -> dict | None:
        """Change a ticket's status, keeping worker state consistent.

        - completed: free the worker and hand them the oldest open ticket.
        - open: unassign the ticket and free the worker (no hand-off, so the
          reopened ticket isn't immediately given straight back to them).
        A worker is only freed if this ticket was still occupying them; e.g.
        reopening an old completed ticket must not free a worker who has
        since moved on to something else.
        """
        with self._lock:
            ticket = self._get_ticket(ticket_id)
            if not ticket:
                return None

            updates = {"status": status}
            if status == "open":
                updates.update(assigned_worker_id=None, assigned_worker_name=None)
            updated = self._update_ticket(ticket_id, updates)
            if not updated:
                return None

            worker_id = ticket.get("assigned_worker_id")
            if worker_id and ticket.get("status") in ACTIVE_STATUSES and status in ("open", "completed"):
                self._update_worker(worker_id, {"status": "available"})
                if status == "completed":
                    try:
                        self._assign_next_ticket_to(worker_id)
                    except DatabaseUnavailable:
                        pass  # the status change itself succeeded; hand-off happens next time

            return updated

    # ------------------------------------------------------------------
    # Workers
    # ------------------------------------------------------------------

    def _create_worker(self, worker: WorkerCreate) -> dict | None:
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
            data["id"] = str(uuid.uuid4())
            data["status"] = "available"
            data["created_at"] = _now_iso()
            with self._lock:
                self._workers.append(data)
            return dict(data)

    def _get_workers(self):
        if self.is_connected():
            try:
                response = self._client.table("workers").select("*").order(
                    "created_at"
                ).execute()
                return response.data
            except Exception as e:
                print(f"❌ Supabase error: {e}")
                raise DatabaseUnavailable(str(e)) from e
        else:
            with self._lock:
                return [dict(w) for w in self._workers]

    def _get_worker(self, worker_id: str) -> dict | None:
        for w in self._get_workers():
            if w.get("id") == worker_id:
                return w
        return None

    def _update_worker(self, worker_id: str, updates: dict) -> dict | None:
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
            with self._lock:
                for w in self._workers:
                    if w.get("id") == worker_id:
                        w.update(updates)
                        return dict(w)
            return None

    # ------------------------------------------------------------------
    # Assignment logic
    # ------------------------------------------------------------------

    def _find_available_worker(self) -> dict | None:
        for w in self._get_workers():
            if w.get("status") == "available":
                return w
        return None

    def _try_auto_assign(self, ticket_id: str) -> dict | None:
        """Called right after a ticket is created. If a worker is free,
        assign them immediately and flip the ticket to in_progress.
        If nobody's free, leave the ticket open — returns None."""
        # A concurrent complete_ticket may already have handed this ticket to a
        # worker between our insert and acquiring the lock; don't assign it twice.
        ticket = self._get_ticket(ticket_id)
        if not ticket or ticket.get("status") != "open" or ticket.get("assigned_worker_id"):
            return None

        worker = self._find_available_worker()
        if not worker:
            return None

        updated = self._update_ticket(ticket_id, {
            "status": "in_progress",
            "assigned_worker_id": worker["id"],
            "assigned_worker_name": worker.get("name"),
        })
        if updated:
            self._update_worker(worker["id"], {"status": "busy"})
        return updated

    def _assign_next_ticket_to(self, worker_id: str) -> dict | None:
        """Find the oldest still-open ticket and hand it to this (now-free) worker."""
        open_tickets = [t for t in self._get_tickets() if t.get("status") == "open"]
        if not open_tickets:
            return None

        # Oldest first. created_at is an ISO-8601 UTC timestamp, which sorts correctly as a string.
        open_tickets.sort(key=lambda t: t.get("created_at") or "")
        oldest = open_tickets[0]

        worker = self._get_worker(worker_id)
        updated = self._update_ticket(oldest["id"], {
            "status": "in_progress",
            "assigned_worker_id": worker_id,
            "assigned_worker_name": worker.get("name") if worker else None,
        })
        if updated:
            self._update_worker(worker_id, {"status": "busy"})
        return updated

# Singleton instance
db = Database()