from datetime import datetime

from pydantic import BaseModel


class CapabilityIn(BaseModel):
    base_url: str = ""
    api_key: str | None = None
    model: str = ""


class WebSearchIn(BaseModel):
    enabled: bool = False
    api_key: str | None = None
    provider: str = ""


class AIConfigIn(BaseModel):
    chat: CapabilityIn = CapabilityIn()
    embed: CapabilityIn = CapabilityIn()
    vision: CapabilityIn = CapabilityIn()
    rerank: CapabilityIn = CapabilityIn()
    web_search: WebSearchIn = WebSearchIn()


class CapabilityOut(BaseModel):
    base_url: str = ""
    model: str = ""
    api_key_set: bool = False
    api_key_masked: str = ""


class WebSearchOut(BaseModel):
    enabled: bool = False
    provider: str = ""
    api_key_set: bool = False
    api_key_masked: str = ""


class AIConfigOut(BaseModel):
    chat: CapabilityOut
    embed: CapabilityOut
    vision: CapabilityOut
    rerank: CapabilityOut
    web_search: WebSearchOut
    updated_at: datetime | None = None
