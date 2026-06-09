from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
import uuid


class Message(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    project_id: Optional[str] = None
    task_id: Optional[str] = None
    channel: str = "system"
    content: str = ""
    direction: str = "out"
    created_at: Optional[datetime] = None
