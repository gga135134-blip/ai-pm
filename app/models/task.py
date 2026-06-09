from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
import uuid


class TaskCreate(BaseModel):
    project_id: str
    parent_task_id: Optional[str] = None
    title: str
    description: str = ""
    assignee: str = ""
    ai_model: str = "auto"
    priority: int = 3
    needs_human: bool = False


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    assignee: Optional[str] = None
    ai_model: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[int] = None
    result: Optional[str] = None
    progress: Optional[int] = None
    needs_human: Optional[bool] = None


class Task(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str
    parent_task_id: Optional[str] = None
    title: str
    description: str = ""
    assignee: str = ""
    ai_model: str = "auto"
    status: str = "pending"
    priority: int = 3
    result: str = ""
    progress: int = 0
    needs_human: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    subtasks: list = []
