"""/v1/advertisements: karbon-admin's advertisements and the copies it publishes to businesses. camelCase."""

from pydantic import BaseModel, ConfigDict, Field

URL = r"^(https://[^\s]+)?$"


class AdFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=80)
    body: str = Field(default="", max_length=400)
    buttonLabel: str = Field(default="", max_length=40)
    buttonUrl: str = Field(default="", max_length=500, pattern=URL)
    background: str = Field(default="ink", description="A theme colour token, or `image`.")
    icon: bool = Field(default=False, description="An icon image is uploaded (advertisements/{id}/icon).")
    active: bool = True


class AdPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=80)
    body: str | None = Field(default=None, max_length=400)
    buttonLabel: str | None = Field(default=None, max_length=40)
    buttonUrl: str | None = Field(default=None, max_length=500, pattern=URL)
    background: str | None = None
    icon: bool | None = None
    active: bool | None = None


class AdPublish(BaseModel):
    businessIds: list[str] = Field(min_length=1, max_length=500)
