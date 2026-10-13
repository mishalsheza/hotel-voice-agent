import uuid
from datetime import datetime

from backend.database import db
from backend.models import TicketCreate, WorkerCreate


def make_ticket(**overrides):
    data = dict(room="402", request_type="housekeeping", item="towels",
                quantity=2, priority="medium", description="more towels")
    data.update(overrides)
    return TicketCreate(**data)


async def test_create_ticket_in_memory():
    saved = await db.create_ticket(make_ticket())

    uuid.UUID(saved["id"])  # a real uuid4, not hash(...)
    created = datetime.fromisoformat(saved["created_at"])
    assert created.tzinfo is not None and created.utcoffset().total_seconds() == 0
    assert saved["status"] == "open"
    assert saved["assigned_worker_id"] is None


async def test_identical_tickets_get_distinct_ids():
    a = await db.create_ticket(make_ticket())
    b = await db.create_ticket(make_ticket())
    assert a["id"] != b["id"]


async def test_get_and_update_ticket():
    saved = await db.create_ticket(make_ticket())

    updated = await db.update_ticket(saved["id"], {"room": "405", "quantity": 4})
    assert updated["room"] == "405" and updated["quantity"] == 4

    fetched = await db.get_ticket(saved["id"])
    assert fetched["room"] == "405"
    assert fetched["item"] == "towels"
    assert len(await db.get_tickets()) == 1


async def test_update_unknown_ticket_returns_none():
    assert await db.update_ticket("missing", {"room": "1"}) is None
    assert await db.get_ticket("missing") is None


async def test_update_with_no_changes_returns_none():
    saved = await db.create_ticket(make_ticket())
    assert await db.update_ticket(saved["id"], {}) is None


async def test_worker_auto_assignment_and_handoff_on_completion():
    worker = await db.create_worker(WorkerCreate(name="Sam"))
    uuid.UUID(worker["id"])

    first = await db.create_ticket(make_ticket(item="towels"))
    assert first["status"] == "in_progress"
    assert first["assigned_worker_id"] == worker["id"]

    second = await db.create_ticket(make_ticket(item="pillows"))
    assert second["status"] == "open"  # Sam is busy

    await db.complete_ticket(first["id"])

    assert (await db.get_ticket(first["id"]))["status"] == "completed"
    handed_off = await db.get_ticket(second["id"])
    assert handed_off["status"] == "in_progress"
    assert handed_off["assigned_worker_name"] == "Sam"
    [sam] = await db.get_workers()
    assert sam["status"] == "busy"


async def test_auto_assign_skips_ticket_already_assigned():
    worker = await db.create_worker(WorkerCreate(name="Sam"))
    await db.create_worker(WorkerCreate(name="Alex"))
    ticket = await db.create_ticket(make_ticket())
    assert ticket["assigned_worker_id"] == worker["id"]

    # Simulates the race where complete_ticket hands the ticket over before
    # create_ticket's own auto-assign runs: it must not be assigned twice.
    assert db._try_auto_assign(ticket["id"]) is None
    assert (await db.get_ticket(ticket["id"]))["assigned_worker_id"] == worker["id"]
    assert [w["status"] for w in await db.get_workers()] == ["busy", "available"]


async def test_concurrent_creates_never_double_book_a_worker():
    import asyncio

    await db.create_worker(WorkerCreate(name="Sam"))
    await db.create_worker(WorkerCreate(name="Alex"))

    await asyncio.gather(*(db.create_ticket(make_ticket(item=f"item {i}")) for i in range(20)))

    tickets = await db.get_tickets()
    assert len(tickets) == 20
    assigned = [t["assigned_worker_id"] for t in tickets if t["status"] == "in_progress"]
    assert len(assigned) == 2 and len(set(assigned)) == 2
    assert all(w["status"] == "busy" for w in await db.get_workers())


async def test_reads_return_copies():
    saved = await db.create_ticket(make_ticket())
    [listed] = await db.get_tickets()
    listed["room"] = "999"
    assert (await db.get_ticket(saved["id"]))["room"] == "402"


async def test_ticket_still_saved_when_auto_assign_hits_outage(monkeypatch):
    from backend.database import DatabaseUnavailable

    def outage(ticket_id):
        raise DatabaseUnavailable("down")

    monkeypatch.setattr(db, "_try_auto_assign", outage)
    saved = await db.create_ticket(make_ticket())
    assert saved and saved["status"] == "open"
