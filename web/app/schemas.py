"""Strict API contracts. Password values are deliberately never stripped."""
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ID = Annotated[int, Field(strict=True, gt=0)]
Timestamp = Annotated[int, Field(strict=True, ge=0, le=253402300799999)]

class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("name", "title", mode="before", check_fields=False)
    @classmethod
    def nonblank(cls, value):
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("Must not be blank")
        return value

    @model_validator(mode="after")
    def clear_flags(self):
        for flag, name in (("clear_start", "start_at"), ("clear_end", "end_at"), ("clear_stream", "stream_id")):
            if getattr(self, flag, False) and getattr(self, name, None) is not None:
                raise ValueError(f"Do not combine {flag} and {name}")
        return self

class StreamBody(InputModel):
    name: str = Field(min_length=1, max_length=60)


class StreamPatch(InputModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    fallback: str | None = Field(default=None, pattern="^(screensaver|blank)$")


class OrderBody(InputModel):
    placement_ids: list[ID] = Field(max_length=1000)


class DisplayBody(InputModel):
    name: str | None = Field(default=None, max_length=60)
    slug: str | None = Field(default=None, min_length=1, max_length=40)
    stream_id: ID | None = None
    clear_stream: bool = False


class UploadPatch(InputModel):
    title: str = Field(min_length=1, max_length=120)


class PlacementBody(InputModel):
    upload_id: ID
    stream_ids: list[ID] = Field(default_factory=list, max_length=256)
    all_streams: bool = False
    mode: str = Field(pattern="^(rotation|override)$")
    slide_seconds: int = Field(default=10, ge=2, le=3600)
    start_at: Timestamp | None = None
    end_at: Timestamp | None = None


class PlacementPatch(InputModel):
    slide_seconds: int | None = Field(default=None, ge=2, le=3600)
    start_at: Timestamp | None = None
    end_at: Timestamp | None = None
    clear_start: bool = False
    clear_end: bool = False
    enabled: bool | None = None


class NewUser(InputModel):
    username: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9._@-]+$")
    password: str = Field(min_length=1, max_length=128)


class SetupBody(InputModel):
    site_name: str = Field(min_length=1, max_length=80)
    username: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9._@-]+$")
    password: str = Field(min_length=1, max_length=128)
    setup_code: str = Field(default="", max_length=128)


class NewPassword(InputModel):
    password: str = Field(min_length=1, max_length=128)



class FeedBody(InputModel):
    title: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=1, max_length=2048)
    kind: str = Field(pattern="^(hls|remote_video|remote_image|web)$")


class ShareImport(InputModel):
    as_graphic: Annotated[bool, Field(strict=True)] = False
    path: str = Field(min_length=1, max_length=2048)
    title: str = Field(default="", max_length=120)


class ShareExport(InputModel):
    upload_id: ID
    path: str = Field(default="", max_length=2048)
