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
    CLASSIFYING = "classifying"
    CLASSIFIED = "classified"  # [CHECKPOINT étape 1] plan de réorg proposé, en attente de validation humaine
    REORGANIZING = "reorganizing"
    REORGANIZED = "reorganized"  # copie triée appliquée — prêt pour l'étape 2 (écran de sélection des pièces)
    ANALYZING_COMPLETENESS = "analyzing_completeness"
    COMPLETENESS_REVIEW = "completeness_review"  # [CHECKPOINT étape 2] résultats proposés, en attente de validation
    COMPLETENESS_VALIDATED = "completeness_validated"  # étape 2 validée — prêt pour l'étape 3
    EXTRACTING = "extracting"
    EXTRACTION_REVIEW = "extraction_review"  # [CHECKPOINT étape 3] valeurs proposées, en attente de validation
    EXTRACTION_VALIDATED = "extraction_validated"  # étape 3 validée — analyse du DCE terminée
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
    # Aucun OCR tenté (texte natif absent ou insuffisant) : à ré-extraire à la demande si le
    # document s'avère concerné par une étape ultérieure (§ ensure_document_ocr, extraction).
    DEFERRED = "deferred"


class CacheStatus(str, enum.Enum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"


class ClassificationStatus(str, enum.Enum):
    PENDING = "pending"  # pas encore classifié
    PROPOSED = "proposed"  # proposition du moteur (3 signaux), pas encore revue
    CORRECTED = "corrected"  # corrigée manuellement par l'utilisateur (checkpoint)
    ERROR = "error"


class CompletenessStatus(str, enum.Enum):
    PENDING = "pending"  # pas encore analysée (ou décochée par l'utilisateur)
    PROPOSED = "proposed"  # proposition du moteur (3 couches), pas encore revue
    CORRECTED = "corrected"  # corrigée manuellement par l'utilisateur (checkpoint)
    ERROR = "error"


class MatchLayer(str, enum.Enum):
    FILE = "file"  # couche 1 : correspondance directe par fichier classifié
    CONTENT = "content"  # couche 2 : correspondance intra-document par mots-clés
    LLM = "llm"  # couche 3 : confirmée par vérification LLM sur un passage candidat
    NONE = "none"  # aucune correspondance trouvée


class Presence(str, enum.Enum):
    PRESENT = "present"
    PARTIAL = "partial"
    ABSENT = "absent"


class Certainty(str, enum.Enum):
    CERTAIN = "certain"
    PROBABLE = "probable"
    A_VERIFIER = "a_verifier"


class ExtractionStatus(str, enum.Enum):
    PENDING = "pending"  # pas encore analysé
    PROPOSED = "proposed"  # proposition du moteur, pas encore revue
    CORRECTED = "corrected"  # corrigée manuellement par l'utilisateur (checkpoint)
    ERROR = "error"


class CrossCheckStatus(str, enum.Enum):
    COHERENT = "coherent"  # ≥2 documents de référence concordants
    INCOHERENT = "incoherent"  # ≥2 documents de référence divergents — à trancher humainement
    SINGLE_SOURCE = "single_source"  # un seul document de référence disponible, pas de recoupement possible
    NOT_APPLICABLE = "not_applicable"  # champ non soumis au recoupement (§ models.yaml)


class Dossier(Base):
    __tablename__ = "dossiers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    original_filename: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(32), default=DossierStatus.UPLOADED.value)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_step: Mapped[int] = mapped_column(Integer, default=0)

    # Hash du .zip uploadé (pas du contenu individuel des documents, cf. Document.sha256) —
    # sert uniquement à détecter un ré-upload probable du même dossier (§ upload_dossier).
    upload_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Instantané dénormalisé du dossier existant dont celui-ci semble être une copie, capturé
    # une fois à l'upload — un avertissement non bloquant, jamais un refus d'upload.
    duplicate_of_dossier_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    duplicate_of_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    duplicate_of_created_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    total_files: Mapped[int] = mapped_column(Integer, default=0)
    files_text_extracted: Mapped[int] = mapped_column(Integer, default=0)
    files_non_analyzable: Mapped[int] = mapped_column(Integer, default=0)
    # Sous-ensemble de files_non_analyzable dont le contenu est potentiellement pertinent
    # mais inaccessible (archive protégée/corrompue, extension inconnue/non supportée) — par
    # opposition aux cas anodins (plans, fichiers système) mêlés dans le même compteur global.
    files_non_analyzable_at_risk: Mapped[int] = mapped_column(Integer, default=0)
    files_error: Mapped[int] = mapped_column(Integer, default=0)

    files_classified: Mapped[int] = mapped_column(Integer, default=0)

    # Rapport de réorganisation (§4.4), écrit à l'application de la copie triée
    reorg_report_json_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    reorg_report_md_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    reorg_applied_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Compteurs étape 2 (§5) — parmi les pièces sélectionnées par l'utilisateur
    pieces_selected: Mapped[int] = mapped_column(Integer, default=0)
    pieces_checked: Mapped[int] = mapped_column(Integer, default=0)
    pieces_present: Mapped[int] = mapped_column(Integer, default=0)
    pieces_absent: Mapped[int] = mapped_column(Integer, default=0)
    pieces_error: Mapped[int] = mapped_column(Integer, default=0)

    # Rapport de complétude (§5.5), écrit à la validation du checkpoint étape 2
    completeness_report_json_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    completeness_report_md_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    completeness_validated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Compteurs étape 3 (§6) — sur l'intégralité du schéma d'extraction (pas de sélection)
    fields_total: Mapped[int] = mapped_column(Integer, default=0)
    fields_extracted: Mapped[int] = mapped_column(Integer, default=0)
    fields_present: Mapped[int] = mapped_column(Integer, default=0)
    fields_absent: Mapped[int] = mapped_column(Integer, default=0)
    fields_incoherent: Mapped[int] = mapped_column(Integer, default=0)
    fields_error: Mapped[int] = mapped_column(Integer, default=0)

    # Rapport d'extraction (§6.4), écrit à la validation du checkpoint étape 3
    extraction_report_json_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    extraction_report_md_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    extraction_validated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Synthèse textuelle (vision globale du dossier), générée en un seul appel LLM en fin
    # d'étape 3 à partir des valeurs déjà extraites (jamais une relecture des documents bruts)
    synthese_ia: Mapped[str | None] = mapped_column(Text, nullable=True)
    synthese_ia_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    synthese_ia_generated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Synthèse projet — Phase 1 du protocole d'analyse (refs/PHASE ANALYSE/00_PROTOCOLE.md),
    # rapport narratif exhaustif (identité, économie, équipe, RICT, géotechnique…) relisant
    # directement les documents pivots (app/synthesis/) — distincte de `synthese_ia` ci-dessus.
    # Générée à la demande de l'expert, jamais automatiquement enchaînée à l'étape 3.
    synthese_projet_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    synthese_projet_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    synthese_projet_status: Mapped[str] = mapped_column(String(16), default="not_generated")
    synthese_projet_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    synthese_projet_generated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    documents: Mapped[list["Document"]] = relationship(back_populates="dossier")
    completeness_checks: Mapped[list["CompletenessCheck"]] = relationship(back_populates="dossier")
    extraction_results: Mapped[list["ExtractionResult"]] = relationship(back_populates="dossier")


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
    # Vrai si le contenu est potentiellement pertinent mais inaccessible (cf. Dossier.files_non_analyzable_at_risk)
    non_analyzable_at_risk: Mapped[bool] = mapped_column(default=False)

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

    # --- Étape 1 : classification + réorganisation (§4) -----------------------------
    classification_status: Mapped[str] = mapped_column(
        String(16), default=ClassificationStatus.PENDING.value
    )
    classification_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Proposition du moteur (3 signaux), jamais écrasée après coup — trace de la décision d'origine
    proposed_category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    proposed_lot: Mapped[str | None] = mapped_column(String(32), nullable=True)
    proposed_doc_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    proposed_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    classification_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    classification_justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON: signaux ayant contribué (mots-clés filename/contenu matchés, sortie brute LLM)
    classification_signals_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    classification_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    classification_model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    classified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Valeurs finales (= proposition par défaut, écrasées par une correction humaine au checkpoint)
    final_category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    final_lot: Mapped[str | None] = mapped_column(String(32), nullable=True)
    final_doc_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    final_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_manually_corrected: Mapped[bool] = mapped_column(default=False)

    # Chemin relatif à workspace/<dossier_id>/organized/ une fois la copie triée appliquée
    organized_relative_path: Mapped[str | None] = mapped_column(Text, nullable=True)

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


class CompletenessCheck(Base):
    """Résultat d'analyse de complétude (§5) pour une pièce (`config/pieces_checklist.yaml`)
    d'un dossier donné. Une pièce peut correspondre à 0, 1 ou plusieurs documents (§5.3, pièce
    noyée dans un autre document) — d'où une table dédiée plutôt que des colonnes sur
    `Document`. Miroir du pattern proposed_*/final_* déjà utilisé pour la classification.
    """

    __tablename__ = "completeness_checks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    dossier_id: Mapped[str] = mapped_column(ForeignKey("dossiers.id"), index=True)
    piece_id: Mapped[str] = mapped_column(String(64))

    # Sélectionnée par l'utilisateur pour ce dossier (écran de sélection, §5.2) avant lancement
    is_selected: Mapped[bool] = mapped_column(default=True)

    status: Mapped[str] = mapped_column(String(16), default=CompletenessStatus.PENDING.value)
    completeness_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    match_layer: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Proposition du moteur (3 couches), jamais écrasée après coup — trace de la décision d'origine
    proposed_presence: Mapped[str | None] = mapped_column(String(16), nullable=True)
    proposed_certainty: Mapped[str | None] = mapped_column(String(16), nullable=True)
    proposed_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    proposed_justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON: liste d'ids Document ayant permis la correspondance (0, 1 ou plusieurs)
    proposed_matched_document_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON: pour les pièces `par_lot`, lots couverts / manquants
    proposed_matched_lots_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    completeness_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    completeness_model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    analyzed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Valeurs finales (= proposition par défaut, écrasées par une correction humaine au checkpoint)
    final_presence: Mapped[str | None] = mapped_column(String(16), nullable=True)
    final_certainty: Mapped[str | None] = mapped_column(String(16), nullable=True)
    is_manually_corrected: Mapped[bool] = mapped_column(default=False)
    corrected_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    dossier: Mapped["Dossier"] = relationship(back_populates="completeness_checks")


class ExtractionResult(Base):
    """Résultat d'extraction (§6) pour un champ (`config/extraction_schema.yaml`) d'un dossier
    donné. Contrairement à `CompletenessCheck`, pas de notion de sélection : le schéma
    d'extraction est fixe, tous les champs sont toujours analysés. Miroir du pattern
    proposed_*/final_* déjà utilisé pour la classification et la complétude.
    """

    __tablename__ = "extraction_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    dossier_id: Mapped[str] = mapped_column(ForeignKey("dossiers.id"), index=True)
    field_id: Mapped[str] = mapped_column(String(64))

    status: Mapped[str] = mapped_column(String(16), default=ExtractionStatus.PENDING.value)
    extraction_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    match_layer: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Proposition du moteur, jamais écrasée après coup — trace de la décision d'origine
    proposed_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    proposed_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    proposed_justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    proposed_citation: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON: liste de {document_id, filename, value, confidence} — un par document interrogé,
    # utile surtout pour afficher le détail d'un conflit de recoupement
    proposed_sources_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Statut de recoupement pour les champs critiques (montants, dates, garanties) — null si
    # le champ n'est pas soumis au recoupement (models.yaml/extraction/cross_check_required_fields)
    cross_check_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    extraction_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    extraction_model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    analyzed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Valeur finale (= proposition par défaut, écrasée par une correction humaine au checkpoint)
    final_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_manually_corrected: Mapped[bool] = mapped_column(default=False)
    corrected_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    dossier: Mapped["Dossier"] = relationship(back_populates="extraction_results")
