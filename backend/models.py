from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class TicketCreate(BaseModel):
    room: str
    request_type: str
    item: str
    quantity: int = 1
    priority: str = "medium"
    description: str

class Worker(BaseModel):
    id: Optional[str] = None
    name: str
    status: str = "available"  # available | busy
    created_at: Optional[datetime] = None
 
class WorkerCreate(BaseModel):
    name: str