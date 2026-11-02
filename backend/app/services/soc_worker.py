import asyncio
import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select

from app.agents.soc_agent import AnalysisOutput
from app.db.database import async_session
from app.db.models import GameSession, SecurityAuditLog

logger = logging.getLogger(__name__)

SOC_BATCH_SIZE = 5


async def fetch_pending_logs() -> list[tuple[UUID, str, dict]]:
    """Read a batch of uninvestigated solo-lab events; the transaction ends before analysis."""
    async with async_session() as db:
        result = await db.execute(
            select(SecurityAuditLog.id, SecurityAuditLog.event_type, SecurityAuditLog.payload)
            .join(GameSession, SecurityAuditLog.lab_instance_id == GameSession.lab_instance_id)
            .where(SecurityAuditLog.investigated_at.is_(None), GameSession.is_solo.is_(True))
            .order_by(SecurityAuditLog.timestamp)
            .limit(SOC_BATCH_SIZE)
        )
        return [tuple(row) for row in result.all()]


async def save_verdict(log_id: UUID, verdict: AnalysisOutput) -> bool:
    """Record a verdict in a short transaction unless the event was already investigated."""
    async with async_session() as db:
        result = await db.execute(
            select(SecurityAuditLog)
            .where(SecurityAuditLog.id == log_id, SecurityAuditLog.investigated_at.is_(None))
            .with_for_update(skip_locked=True)
        )
        log = result.scalars().first()
        if log is None:
            return False
        log.investigated_at = datetime.now(timezone.utc)
        log.is_compromised = verdict.is_malicious
        log.payload = {**(log.payload or {}), "soc_reasoning": verdict.reasoning}
        await db.commit()
        return True


async def run_soc_analyst_loop(soc_graph, queue):
    """
    Continuous background task that polls the database for uninvestigated security logs.
    ONLY analyzes logs for lab instances where 'is_solo' == True.
    No database transaction is open while the model runs.
    """
    logger.info("[SOC Worker] Background SOC Analyst Agent started. Monitoring solo labs...")

    while True:
        try:
            for log_id, event_type, payload in await fetch_pending_logs():
                initial_state = {
                    "log_id": str(log_id),
                    "event_type": event_type,
                    "payload": str(payload),
                    "is_malicious": False,
                    "reasoning": "",
                }
                try:
                    # The local SOC and chat share the same GPU/model instance.
                    async with queue.gpu_lock:
                        final_state = await soc_graph.ainvoke(initial_state)
                    verdict = AnalysisOutput.model_validate(final_state)
                except Exception:
                    logger.exception("[SOC Worker] Analysis failed for log %s; left pending", log_id)
                    continue

                if await save_verdict(log_id, verdict):
                    logger.info(f"[SOC Worker] Log {log_id} analyzed -> is_malicious: {verdict.is_malicious}")
                else:
                    logger.info(f"[SOC Worker] Log {log_id} was investigated meanwhile; verdict discarded")

        except Exception as e:
            logger.error(f"[SOC Worker] Error in analyst loop: {e}")

        await asyncio.sleep(5)
