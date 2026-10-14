"""Auth request/response schemas."""
from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    username: str | None = Field(default=None, min_length=3, max_length=64)
    country: str | None = Field(default=None, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)
    # Required acceptance of terms + age + risk disclaimers
    accepted_terms: bool = Field(
        ...,
        description="User must accept the Terms of Service and privacy policy",
    )
    accepted_age: bool = Field(
        ...,
        description="User confirms they are 18+ or the local age of majority",
    )
    accepted_risk: bool = Field(
        ...,
        description=(
            "User acknowledges that SignalIQ provides statistical analysis, "
            "not betting advice, and that football matches are inherently "
            "unpredictable"
        ),
    )


class LoginRequest(BaseModel):
    identifier: str  # email OR username
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds
    # S.7.4 — true when the org has not completed onboarding.
    onboarding_required: bool = False

class RefreshRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    id: str
    username: str | None = None
    country: str | None = None
    email: str
    full_name: str | None
    email_verified: bool
    is_superadmin: bool

    model_config = {"from_attributes": True}

class MeResponse(BaseModel):
    user: UserResponse
    org_id: str | None
    # S.7.5a — org_slug was being passed by the /me handler but the
    # field did not exist on this model. Pydantic v2 silently drops
    # unknown kwargs on construction (extra="ignore"), so the value
    # was being computed and thrown away. The frontend stores
    # me.org_slug and was getting undefined.
    org_slug: str | None = None
    role: str | None
    # S.7.5a — true when the org has not finished onboarding. The
    # frontend uses this to route after login/register.
    onboarding_required: bool = False
