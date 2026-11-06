import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class PasswordBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_password: str = Field(min_length=12, max_length=128)


class CodeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=6, max_length=32)


class AccountPasswordBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class PunchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: uuid.UUID
    client_reported_at: datetime | None = None


class CorrectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["MISSED_CHECK_IN", "MISSED_CHECK_OUT", "HISTORICAL_PAIR"]
    reason: str = Field(min_length=10, max_length=500)
    requested_start_at: datetime | None = None
    requested_end_at: datetime | None = None


class ReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_reason: str = Field(min_length=10, max_length=500)


class ManagerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    temporary_password: str = Field(min_length=12, max_length=128)
    display_name: str = Field(min_length=2, max_length=120)
    role: Literal["MANAGER", "ADMIN"] = "MANAGER"
    employee_number: str | None = Field(default=None, max_length=32)
    designation: str | None = Field(default=None, max_length=120)
    department_name: str | None = Field(default=None, max_length=120)
    device_pin: str | None = Field(default=None, max_length=14)
    remote_attendance_enabled: bool = False
    is_test_record: bool


class ManagerPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = Field(default=None, min_length=2, max_length=120)
    designation: str | None = Field(default=None, max_length=120)
    department_name: str | None = Field(default=None, max_length=120)
    employee_number: str | None = Field(default=None, max_length=32)
    device_pin: str | None = Field(default=None, max_length=14)
    remote_attendance_enabled: bool | None = None
    is_test_record: bool | None = None


class PolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    remote_attendance_approved: bool | None = None
    confirmation: str | None = None
    working_days: list[Literal["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]] | None = None
    work_start_local: str | None = None
    work_end_local: str | None = None
    grace_minutes: int | None = Field(default=None, ge=0, le=180)
    inference_enabled: bool | None = None
    enforce_working_days: bool | None = None
    passkey_required_for_attendance: bool | None = None
    policy_note: str | None = Field(default=None, max_length=500)


class DeviceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=120)
    model: str = Field(min_length=2, max_length=80)
    serial_number: str = Field(min_length=2, max_length=64)
    firmware: str | None = Field(default=None, max_length=80)
    platform: str | None = Field(default=None, max_length=80)
    mac_address: str | None = Field(default=None, max_length=32)
    location: str | None = Field(default=None, max_length=120)
    protocol: Literal["ADMS", "TA_PUSH", "AC_PUSH", "UNKNOWN"] = "UNKNOWN"
    notes: str | None = Field(default=None, max_length=1000)


class DevicePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=2, max_length=120)
    firmware: str | None = Field(default=None, max_length=80)
    location: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=1000)
    status: Literal["NOT_CONNECTED", "DISABLED"] | None = None


class MappingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    employee_id: uuid.UUID
    device_user_pin: str = Field(min_length=1, max_length=14)
