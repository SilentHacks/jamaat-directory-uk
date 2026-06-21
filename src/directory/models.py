import json

from sqlalchemy import Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Mosque(Base):
    __tablename__ = "mosque"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    aliases: Mapped[str] = mapped_column(String, nullable=False, default="[]")
    address: Mapped[str | None] = mapped_column(String)
    city: Mapped[str | None] = mapped_column(String)
    postcode: Mapped[str | None] = mapped_column(String)
    country: Mapped[str] = mapped_column(String, nullable=False, default="GB")
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)
    website_url: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    created_at: Mapped[str | None] = mapped_column(String, server_default=func.now())
    updated_at: Mapped[str | None] = mapped_column(String, server_default=func.now())

    @property
    def aliases_list(self) -> list[str]:
        return json.loads(self.aliases or "[]")


class Source(Base):
    __tablename__ = "source"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    mosque_id: Mapped[str] = mapped_column(ForeignKey("mosque.id"), nullable=False)
    url: Mapped[str | None] = mapped_column(String)
    platform: Mapped[str | None] = mapped_column(String)
    shape: Mapped[str | None] = mapped_column(String)
    config: Mapped[str | None] = mapped_column(String)
    requires_js: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    triage_status: Mapped[str] = mapped_column(String, nullable=False, default="candidate")
    confidence: Mapped[float | None] = mapped_column(Float)
    review_reason: Mapped[str | None] = mapped_column(String)
    flags: Mapped[str | None] = mapped_column(String)  # JSON array of strings
    authored_by: Mapped[str | None] = mapped_column(String)
    authored_at: Mapped[str | None] = mapped_column(String)
    source_html_hash: Mapped[str | None] = mapped_column(String)
    last_fetched_at: Mapped[str | None] = mapped_column(String)
    last_status: Mapped[str | None] = mapped_column(String)
    last_error: Mapped[str | None] = mapped_column(String)


class Occurrence(Base):
    __tablename__ = "occurrence"

    mosque_id: Mapped[str] = mapped_column(ForeignKey("mosque.id"), primary_key=True)
    date: Mapped[str] = mapped_column(String, primary_key=True)
    prayer: Mapped[str] = mapped_column(String, primary_key=True)
    session_idx: Mapped[int] = mapped_column(Integer, primary_key=True, default=0)
    jamaah_time: Mapped[str] = mapped_column(String, nullable=False)
    begin_time: Mapped[str | None] = mapped_column(String)
    label: Mapped[str | None] = mapped_column(String)
    source_id: Mapped[str | None] = mapped_column(ForeignKey("source.id"))
    extracted_at: Mapped[str | None] = mapped_column(String, server_default=func.now())


class ExtractorRun(Base):
    __tablename__ = "extractor_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str | None] = mapped_column(ForeignKey("source.id"))
    started_at: Mapped[str | None] = mapped_column(String, server_default=func.now())
    ok: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(String)
