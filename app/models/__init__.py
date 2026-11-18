"""Model package: import order establishes FK target visibility for Alembic autogenerate.

Catalog/assets first (no dependency on campaign/strategy), then campaign
tables (reference catalog/assets), then strategy tables (reference catalog)
and the campaign<->strategy link table last.
"""
from app.models.assets import Asset, ObjectAsset
from app.models.campaign import (
    Campaign,
    CampaignAvengerCell,
    CampaignContentSource,
    CampaignEvent,
    CampaignObjectState,
    CampaignPlan,
    CampaignPlanItem,
    CampaignResearchQueueItem,
    CampaignSoldier,
    CampaignSoldierLoadout,
    CampaignStrategyProfile,
)
from app.models.catalog import (
    CatalogAttributeDefinition,
    CatalogObject,
    CatalogObjectTag,
    CatalogObjectType,
    CatalogPrerequisiteGroup,
    CatalogPrerequisiteGroupMember,
    CatalogRelationship,
    CatalogRelationshipType,
    CatalogSoldierStatProfile,
    ContentSource,
    ContentSourceRelation,
)
from app.models.strategy import StrategyProfile, StrategyRecommendation

__all__ = [
    "Asset",
    "Campaign",
    "CampaignAvengerCell",
    "CampaignContentSource",
    "CampaignEvent",
    "CampaignObjectState",
    "CampaignPlan",
    "CampaignPlanItem",
    "CampaignResearchQueueItem",
    "CampaignSoldier",
    "CampaignSoldierLoadout",
    "CampaignStrategyProfile",
    "CatalogAttributeDefinition",
    "CatalogObject",
    "CatalogObjectTag",
    "CatalogObjectType",
    "CatalogPrerequisiteGroup",
    "CatalogPrerequisiteGroupMember",
    "CatalogRelationship",
    "CatalogRelationshipType",
    "CatalogSoldierStatProfile",
    "ContentSource",
    "ContentSourceRelation",
    "ObjectAsset",
    "StrategyProfile",
    "StrategyRecommendation",
]
