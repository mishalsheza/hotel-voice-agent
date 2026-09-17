from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class Ticket(BaseModel):
    id: Optional[str] = None
    room: str
    request_type: str  # housekeeping, maintenance, wakeup_call
    item: str
    quantity: int = 1
    priority: str = "medium"  # low, medium, high
    description: str
    status: str = "open"  # open, assigned, in_progress, completed
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

class TicketCreate(BaseModel):
    room: str
    request_type: str
    item: str
    quantity: int = 1
    priority: str = "medium"
    description: str

class WakeupCall(BaseModel):
    room: str
    time: str
