"""Tabla: users (futuro - B-002 autenticacion JWT/roles)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    full_name: Mapped[Optional[str]] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(20), default="OPERATOR")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
