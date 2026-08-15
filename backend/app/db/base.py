"""SQLAlchemy declarative base.

Models will subclass ``Base`` in later steps. Alembic's ``env.py`` imports
``Base.metadata`` as its autogenerate target.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all ORM models."""
