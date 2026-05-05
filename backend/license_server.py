"""VoiceUse License Server — FastAPI backend for license management.

Deploy this to a GCP VM (or Cloud Run) behind HTTPS.  It handles:
- License key activation / deactivation
- Machine fingerprint validation
- Token-based validation from desktop clients

## Local Development

    cd backend
    pip install -r requirements.txt
    uvicorn license_server:app --reload --port 8000

## Environment Variables

    DATABASE_URL=postgresql://user:pass@localhost/voiceuse
    REDIS_URL=redis://localhost:6379/0
    SECRET_KEY=change-me-in-production
    ADMIN_API_KEY=admin-secret-for-key-generation
"""

import hashlib
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, Column, String, DateTime, Integer, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./license_server.db")
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "admin-dev-secret")
TRIAL_DAYS = 7

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class LicenseKeyDB(Base):
    """A license key that can be activated on N machines."""
    __tablename__ = "license_keys"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, index=True, nullable=False)
    tier = Column(String, default="pro")  # pro, enterprise
    max_machines = Column(Integer, default=3)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime, nullable=True)  # null = never
    revoked = Column(Boolean, default=False)


class MachineActivationDB(Base):
    """A single machine activation for a license key."""
    __tablename__ = "machine_activations"

    id = Column(Integer, primary_key=True, index=True)
    license_key = Column(String, index=True, nullable=False)
    machine_id = Column(String, index=True, nullable=False)
    activated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_seen = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    token = Column(String, unique=True, nullable=False)


Base.metadata.create_all(bind=engine)


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="VoiceUse License Server",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
)


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_admin(api_key: str = Header(..., alias="X-Admin-Key")) -> None:
    if api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid admin key")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ActivateRequest(BaseModel):
    license_key: str
    machine_id: str


class ActivateResponse(BaseModel):
    token: str
    status: str
    tier: str
    expires_at: Optional[str] = None


class ValidateRequest(BaseModel):
    token: str
    machine_id: str


class ValidateResponse(BaseModel):
    valid: bool
    status: str
    tier: str
    expires_at: Optional[str] = None


class DeactivateRequest(BaseModel):
    token: str
    machine_id: str


class GenerateKeyRequest(BaseModel):
    count: int = Field(1, ge=1, le=100)
    tier: str = "pro"
    max_machines: int = 3
    expires_days: Optional[int] = None


class GenerateKeyResponse(BaseModel):
    keys: List[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def generate_license_key() -> str:
    """Generate a VU-XXXX-XXXX-XXXX-XXXX key."""
    parts = [secrets.token_hex(2).upper() for _ in range(4)]
    return f"VU-{'-'.join(parts)}"


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/v1/license/activate", response_model=ActivateResponse)
def activate(req: ActivateRequest, db: Session = Depends(get_db)):
    """Activate a license key on a machine. Returns a token for validation."""
    key_record = db.query(LicenseKeyDB).filter(LicenseKeyDB.key == req.license_key).first()

    if not key_record:
        raise HTTPException(status_code=404, detail="License key not found")

    if key_record.revoked:
        raise HTTPException(status_code=403, detail="License key revoked")

    if key_record.expires_at and now_utc() > key_record.expires_at:
        raise HTTPException(status_code=403, detail="License key expired")

    # Check existing activation for this machine
    existing = db.query(MachineActivationDB).filter(
        MachineActivationDB.license_key == req.license_key,
        MachineActivationDB.machine_id == req.machine_id,
    ).first()

    if existing:
        # Re-activation — update last_seen and return same token
        existing.last_seen = now_utc()
        db.commit()
        return ActivateResponse(
            token=existing.token,
            status="active",
            tier=key_record.tier,
            expires_at=key_record.expires_at.isoformat() if key_record.expires_at else None,
        )

    # Check machine limit
    count = db.query(MachineActivationDB).filter(
        MachineActivationDB.license_key == req.license_key
    ).count()
    if count >= key_record.max_machines:
        raise HTTPException(
            status_code=403,
            detail=f"Machine limit reached ({key_record.max_machines}). Deactivate another machine first.",
        )

    token = generate_token()
    activation = MachineActivationDB(
        license_key=req.license_key,
        machine_id=req.machine_id,
        token=token,
    )
    db.add(activation)
    db.commit()

    return ActivateResponse(
        token=token,
        status="active",
        tier=key_record.tier,
        expires_at=key_record.expires_at.isoformat() if key_record.expires_at else None,
    )


@app.post("/v1/license/validate", response_model=ValidateResponse)
def validate(req: ValidateRequest, db: Session = Depends(get_db)):
    """Validate a token + machine_id pair."""
    activation = db.query(MachineActivationDB).filter(
        MachineActivationDB.token == req.token,
        MachineActivationDB.machine_id == req.machine_id,
    ).first()

    if not activation:
        return ValidateResponse(valid=False, status="invalid", tier="free")

    key_record = db.query(LicenseKeyDB).filter(
        LicenseKeyDB.key == activation.license_key
    ).first()

    if not key_record or key_record.revoked:
        return ValidateResponse(valid=False, status="revoked", tier="free")

    if key_record.expires_at and now_utc() > key_record.expires_at:
        return ValidateResponse(valid=False, status="expired", tier="free")

    activation.last_seen = now_utc()
    db.commit()

    return ValidateResponse(
        valid=True,
        status="active",
        tier=key_record.tier,
        expires_at=key_record.expires_at.isoformat() if key_record.expires_at else None,
    )


@app.post("/v1/license/deactivate")
def deactivate(req: DeactivateRequest, db: Session = Depends(get_db)):
    """Deactivate a machine."""
    activation = db.query(MachineActivationDB).filter(
        MachineActivationDB.token == req.token,
        MachineActivationDB.machine_id == req.machine_id,
    ).first()

    if not activation:
        raise HTTPException(status_code=404, detail="Activation not found")

    db.delete(activation)
    db.commit()
    return {"status": "deactivated"}


@app.post("/admin/generate-keys", response_model=GenerateKeyResponse, dependencies=[Depends(require_admin)])
def generate_keys(req: GenerateKeyRequest, db: Session = Depends(get_db)):
    """Admin endpoint to generate new license keys."""
    keys = []
    for _ in range(req.count):
        key = generate_license_key()
        while db.query(LicenseKeyDB).filter(LicenseKeyDB.key == key).first():
            key = generate_license_key()

        expires = None
        if req.expires_days:
            expires = now_utc() + timedelta(days=req.expires_days)

        record = LicenseKeyDB(
            key=key,
            tier=req.tier,
            max_machines=req.max_machines,
            expires_at=expires,
        )
        db.add(record)
        keys.append(key)

    db.commit()
    return GenerateKeyResponse(keys=keys)


@app.get("/admin/stats", dependencies=[Depends(require_admin)])
def stats(db: Session = Depends(get_db)):
    """Admin endpoint for basic stats."""
    total_keys = db.query(LicenseKeyDB).count()
    total_activations = db.query(MachineActivationDB).count()
    revoked = db.query(LicenseKeyDB).filter(LicenseKeyDB.revoked == True).count()
    return {
        "total_keys": total_keys,
        "total_activations": total_activations,
        "revoked_keys": revoked,
    }


@app.get("/health")
def health():
    return {"status": "ok"}
