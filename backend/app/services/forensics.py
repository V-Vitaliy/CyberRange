import re
from datetime import datetime
from uuid import UUID

from app.db.repository import AuditLogRepository, GameSessionRepository
from app.db.repository import GameSessionRepository, AuditLogRepository
from app.schemas.blue_team import InvestigateResponse
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession


class MLAnalyzer:
    """
    Placeholder class for future Machine Learning model integration (e.g., scikit-learn).
    The model will run on CPU, consume ~20MB RAM, and classify text in milliseconds.
    Currently using compiled Regex patterns as a temporary heuristic WAF engine.
    """
    def __init__(self):
        # Compiled Regex rules for performance. (?i) enables case-insensitive matching.
        self.sqli_regex = re.compile(r"(?i)(union\s+select|select\s+.*?\s+from|drop\s+table|insert\s+into|1\s*=\s*1|'a'\s*=\s*'a'|--)")
        self.pi_regex = re.compile(r"(?i)(ignore\s+(all\s+)?previous|system\s+prompt|you\s+are\s+now|bypass\s+instructions)")
        self.jwt_regex = re.compile(r"(?i)(\"alg\"\s*:\s*\"none\"|alg:\s*none|verify_signature\s*:\s*false)")

    def predict_is_malicious(self, payload, event_type:str) -> bool:
        """
        Simulates ML prediction.
        Future implementation: return self.model.predict([payload])[0] == 1
        """
        if event_type == "FILE_UPLOAD":
            if payload.get("indexing") == "true":
                return True
            return False

        if event_type == "AUTH_BYPASS_ATTEMPT":
            if payload.get('claimed_role') != 'red_team':
                return True
            return False

        payload_str = str(payload).lower()
        return any([
            self.sqli_regex.search(payload_str),
            self.pi_regex.search(payload_str),
            self.jwt_regex.search(payload_str)
        ])

analyzer = MLAnalyzer()

async def process_investigation(
    db: AsyncSession,
    log_id: UUID,
    is_malicious_claim: bool,
    session_id: UUID,
    user_id: UUID
) -> InvestigateResponse:
    """
    Business logic layer: validates the log, calculates defense points,
    and updates the game economy.
    """
    # 1. Retrieve the log entry
    log_entry = await AuditLogRepository.get_by_id(db, log_id)

    if not log_entry:
        raise HTTPException(status_code=404, detail="Log entry not found")

    if log_entry.investigated_at is not None:
        raise HTTPException(status_code=400, detail="This log has already been investigated")

    # 2. ML / Heuristic Prediction
    actually_malicious = analyzer.predict_is_malicious(log_entry.payload, log_entry.event_type)

    # 3. Points Calculation
    credits_to_award = 0
    message = "Investigation complete."

    if is_malicious_claim and actually_malicious:
        credits_to_award = 3
        message = "Correct! Attack identified. You earned 3 defense points."
        log_entry.is_compromised = True
    elif not is_malicious_claim and not actually_malicious:
        credits_to_award = 1
        message = "Correct! False alarm identified. You earned 1 defense point."
    else:
        credits_to_award = 0
        message = "Incorrect verdict. No points awarded."
        if actually_malicious:
            # Mark it compromised anyway for system metrics/reporting
            log_entry.is_compromised = True

    # 4. Database Updates
    log_entry.investigated_at = datetime.utcnow()
    log_entry.investigated_by = user_id

    game_session = await GameSessionRepository.get_by_id(db, session_id)

    if not game_session:
         raise HTTPException(status_code=404, detail="Game session not found")

    # Update economy budget
    game_session.defense_budget += credits_to_award
    await db.commit()

    return InvestigateResponse(
        success=True,
        message=message,
        credits_awarded=credits_to_award,
        new_balance=game_session.defense_budget
    )