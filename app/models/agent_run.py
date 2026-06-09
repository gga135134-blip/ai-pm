from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
import uuid


class AgentRun(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task_id: str
    model: str
    prompt: str = ""
    response: str = ""
    tokens_used: int = 0
    cost: float = 0.0
    status: str = "running"
    created_at: Optional[datetime] = None
