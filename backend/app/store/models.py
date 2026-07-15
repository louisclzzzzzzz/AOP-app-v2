"""Modèles SQLAlchemy : dossiers (DCE), documents (inventaire), cache de texte/OCR.

Toutes les décisions tracées (§9 du PLAN) : chaque ligne porte confiance, source,
modèle+version et horodatages là où c'est pertinent.
"""
from __future__ import annotations

import datetime as dt
import enum
import uuid

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    pass


class DossierStatus(str, enum.Enum):
    UPLOADED = "uploaded"
    UNZIPPING = "unzipping"
    INVENTORYING = "inventorying"
    EXTRACTING_TEXT = "extracting_text"
    READY_STEP1 = "ready_step1"
    ERROR = "error"


class FileCategory(str, enum.Enum):
    PDF = "pdf"
    DOCX = "docx"
    DOC = "doc"
    IMAGE = "image"
    SPREADSHEET = "spreadsheet"
    ARCHIVE = "archive"
    DEMATERIALISE = "dematerialise"  # .cle/.cry/.iv/.pli/.xml de dépôt
    OTHER = "other"


class DocumentStage(str, enum.Enum):
    DISCOVERED = "discovered"
    TEXT_EXTRACTED = "text_extracted"
    NON_ANALYZABLE = "non_analyzable"
    ERROR = "error"


class TextExtractionMethod(str, enum.Enum):
    NATIVE_PDF = "native_pdf"
    OCR = "ocr"
    MIXED_PDF = "mixed_pdf"  # certaines pages natives, d'autres OCRisées
    DOCX_NATIVE = "docx_native"
    DOC_CONVERTED = "doc_converted"
    SPREADSHEET_NATIVE = "spreadsheet_native"
    NONE = "none"


class CacheStatus(str, enum.Enum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"


class Dossier(Base):
    __tablename__ = "dossiers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    original_filename: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(32), default=DossierStatus.UPLOADED.value)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_step: Mapped[int] = mapped_column(Integer, default=0)

    total_files: Mapped[int] = mapped_column(Integer, default=0)
    files_text_extracted: Mapped[int] = mapped_column(Integer, default=0)
    files_non_analyzable: Mapped[int] = mapped_column(Integer, default=0)
    files_error: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    documents: Mapped[list["Document"]] = relationship(back_populates="dossier")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    dossier_id: Mapped[str] = mapped_column(ForeignKey("dossiers.id"), index=True)

    # Chemin relatif à workspace/<dossier_id>/source/ (POSIX, préserve l'arborescence d'origine)
    relative_path: Mapped[str] = mapped_column(Text)
    filename: Mapped[str] = mapped_column(String(512))
    extension: Mapped[str] = mapped_column(String(32))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)

    category: Mapped[str] = mapped_column(String(32))
    is_analyzable: Mapped[bool] = mapped_column(default=True)
    non_analyzable_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # Traçabilité : si ce document provient d'un zip imbriqué décompressé récursivement
    parent_archive_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id"), nullable=True
    )

    stage: Mapped[str] = mapped_column(String(32), default=DocumentStage.DISCOVERED.value)
    stage_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    text_extraction_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    text_cache_id: Mapped[str | None] = mapped_column(
        ForeignKey("text_cache.id"), nullable=True
    )

    # Métadonnées enrichies (§3.5) : titre détecté, premières lignes, mentions clés
    detected_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    preview_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_mentions_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    dossier: Mapped["Dossier"] = relationship(back_populates="documents")
    text_cache: Mapped["TextCache | None"] = relationship()


class TextCache(Base):
    """Cache persistant de texte extrait (natif ou OCR), clé par hash de contenu.

    Un document identique (même hash SHA256), même dans un autre dossier, réutilise
    l'entrée existante : le texte n'est jamais ré-extrait / ré-OCRisé.
    """

    __tablename__ = "text_cache"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    extension: Mapped[str] = mapped_column(String(32))

    method: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default=CacheStatus.PENDING.value)

    # Confiance moyenne (pertinent surtout pour method=ocr/mixed_pdf)
    avg_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Chemin relatif à workspace/cache/text/ vers le fichier .md contenant le texte complet
    text_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # JSON: liste par page {page_no, method, confidence, char_count} + bounding boxes OCR
    pages_meta_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
