"""WTForms for campaign CRUD (FR-CMP-1) and catalog/content-source CRUD
(FR-SRC-1/6, FR-CAT-1/7, FR-CAT-3)."""
from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    FloatField,
    HiddenField,
    SelectField,
    StringField,
    TextAreaField,
)
from wtforms.validators import DataRequired, Length, Optional

from app.models.campaign import CAMPAIGN_DIFFICULTIES
from app.models.catalog import CONTENT_SOURCE_KINDS


class CampaignForm(FlaskForm):
    name = StringField(
        "Campaign name", validators=[DataRequired(), Length(max=200)]
    )
    game_profile_slug = SelectField("Game profile / ruleset", validators=[Optional()])
    difficulty = SelectField(
        "Difficulty",
        choices=[(d, d.capitalize()) for d in CAMPAIGN_DIFFICULTIES],
        default="commander",
    )
    is_ironman = BooleanField("Iron Man")
    notes = TextAreaField("Notes", validators=[Optional(), Length(max=4000)])


class ContentSourceForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=200)])
    kind = SelectField(
        "Kind",
        choices=[(k, k.replace("_", " ").title()) for k in CONTENT_SOURCE_KINDS],
        default="workshop_mod",
    )
    version = StringField("Version", validators=[Optional(), Length(max=50)], default="1.0.0")
    steam_workshop_id = StringField(
        "Steam Workshop ID (optional)", validators=[Optional(), Length(max=50)]
    )
    description = TextAreaField("Description", validators=[Optional(), Length(max=2000)])
    license = StringField("License", validators=[Optional(), Length(max=200)])
    attribution_text = StringField("Attribution", validators=[Optional(), Length(max=500)])
    homepage_url = StringField("Homepage / Workshop URL", validators=[Optional(), Length(max=500)])


class CatalogObjectForm(FlaskForm):
    source_id = SelectField("Source", coerce=int, validators=[DataRequired()])
    type_id = SelectField("Object type", coerce=int, validators=[DataRequired()])
    name = StringField("Name", validators=[DataRequired(), Length(max=200)])
    template_key = StringField(
        "Template / game ID (optional)", validators=[Optional(), Length(max=200)]
    )
    tier = StringField("Tier (optional)", validators=[Optional(), Length(max=10)])
    tags = StringField("Tags (comma-separated)", validators=[Optional(), Length(max=500)])
    short_description = StringField("Short description", validators=[Optional(), Length(max=300)])
    long_description = TextAreaField("Long description / notes", validators=[Optional(), Length(max=4000)])
    attributes_json = TextAreaField("Attributes (JSON, optional)", validators=[Optional(), Length(max=4000)])


class CatalogRelationshipForm(FlaskForm):
    relationship_type_id = SelectField("Relationship type", coerce=int, validators=[DataRequired()])
    direction = SelectField(
        "Direction",
        choices=[("outbound", "This object \u2192 target"), ("inbound", "Target \u2192 this object")],
        default="outbound",
    )
    target_object_id = HiddenField("Target object", validators=[DataRequired()])
    group_key = StringField("Alternative group key (optional)", validators=[Optional(), Length(max=100)])
    amount = FloatField("Amount (for quantified relationships)", validators=[Optional()])
    unit = StringField("Unit", validators=[Optional(), Length(max=50)])


class ImageUploadForm(FlaskForm):
    role = SelectField(
        "Role",
        choices=[("icon", "Icon"), ("portrait", "Portrait"), ("banner", "Banner"), ("gallery", "Gallery")],
        default="icon",
    )
    source_url = StringField("Source URL (optional, informational)", validators=[Optional(), Length(max=500)])
    license = StringField("License (optional)", validators=[Optional(), Length(max=200)])
    attribution_text = StringField("Attribution (optional)", validators=[Optional(), Length(max=500)])


class ImageFetchForm(FlaskForm):
    """FR-AST-4: fetch-from-URL companion to `ImageUploadForm` -- `url` is
    required here (it IS the image source, not optional provenance)."""

    role = SelectField(
        "Role",
        choices=[("icon", "Icon"), ("portrait", "Portrait"), ("banner", "Banner"), ("gallery", "Gallery")],
        default="icon",
    )
    url = StringField("Image URL", validators=[DataRequired(), Length(max=500)])
    license = StringField("License (optional)", validators=[Optional(), Length(max=200)])
    attribution_text = StringField("Attribution (optional)", validators=[Optional(), Length(max=500)])


class CatalogImportForm(FlaskForm):
    confirm = BooleanField("I have reviewed the validation report and want to import")

