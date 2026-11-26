from pydantic import BaseModel, Field


class AuthLoginRequest(BaseModel):
    pin: str = Field(..., min_length=1, max_length=128)


class AuthStatusResponse(BaseModel):
    auth_enabled: bool
    authenticated: bool
    localhost_bypass: bool = False
