from pydantic import BaseModel, Field
from typing import Literal

class ConversationCreate(BaseModel):
    pass

class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    created_at: str

class ConversationOut(BaseModel):
    id: str
    title: str
    updated_at: str

class ChatRequest(BaseModel):
    content: str = Field(min_length=1, max_length=12000)

class RoleSelection(BaseModel):
    role_key: str = Field(pattern="^(daughter|granddaughter)$")

class RoleImport(BaseModel):
    yaml_text: str = Field(min_length=1, max_length=20000)

class RoleConfigUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    config: dict

class FamilyFactCreate(BaseModel):
    fact_type: str = Field(min_length=1, max_length=40)
    content: str = Field(min_length=1, max_length=1000)
    authorized: bool = True

class DestructiveConfirmation(BaseModel):
    confirmation_phrase: Literal["确认删除", "确认撤回"]

class OtpRequest(BaseModel):
    phone_number: str = Field(pattern=r"^\+?[0-9]{8,18}$")

class FamilyPairRequest(OtpRequest):
    otp: str = Field(pattern=r"^[0-9]{6}$")
    pairing_code: str = Field(pattern=r"^[0-9]{6}$")
    display_name: str = Field(default="家属", min_length=1, max_length=100)

class FamilyLoginRequest(OtpRequest):
    otp: str = Field(pattern=r"^[0-9]{6}$")

class DeviceRefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=32, max_length=128)

class PairingStatusRequest(BaseModel):
    enrollment_secret: str = Field(min_length=32, max_length=128)

class ToolPermissionUpdate(BaseModel):
    role_key: str = Field(pattern="^(daughter|granddaughter)$")
    tool_name: str = Field(min_length=1, max_length=80)
    enabled: bool

class PushSubscriptionCreate(BaseModel):
    endpoint: str = Field(min_length=20, max_length=2000)
    keys: dict
    notify_family: bool = False

class UsageEventCreate(BaseModel):
    session_key: str = Field(min_length=16, max_length=80)
    event_type: Literal["page_open", "voice_start", "retry", "cancel", "voice_complete"]
    conversation_id: str | None = None

class ConfirmationDecision(BaseModel):
    accepted: bool
    trigger: Literal["button"] = "button"

class ChatEvent(BaseModel):
    type: str
    content: str
