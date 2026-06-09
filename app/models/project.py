from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
import uuid


class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    owner: str = ""


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    owner: Optional[str] = None


class Project(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str = ""
    status: str = "draft"
    owner: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    task_count: int = 0
    done_count: int = 0
