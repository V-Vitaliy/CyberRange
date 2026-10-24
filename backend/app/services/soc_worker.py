import asyncio
import logging
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.db.database import async_session
from app.db.models import SecurityAuditLog, GameSession
from app.agents.soc_agent import AnalysisOutput

logger = logging.getLogger(__name__)

async def run_soc_analyst_loop(soc_graph, queue):
    """
    Continuous background task that polls the database for uninvestigated security logs.
    ONLY analyzes logs for lab instances where 'is_solo' == True.
    """
    logger.info("[SOC Worker] Background SOC Analyst Agent started. Monitoring solo labs...")

    while True:
        try:
            async with async_session() as db:
                stmt = (
                    select(SecurityAuditLog)
                    .join(GameSession, SecurityAuditLog.lab_instance_id == GameSession.lab_instance_id)
                    .where(
                        SecurityAuditLog.investigated_at.is_(None),
                        GameSession.is_solo == True
                    )
                    .order_by(SecurityAuditLog.timestamp)
                    .with_for_update(skip_locked=True, of=SecurityAuditLog)
                    .limit(5)
                )
                result = await db.execute(stmt)
                logs_to_process = result.scalars().all()

                for log in logs_to_process:

                    initial_state = {
                        "log_id": str(log.id),
                        "event_type": log.event_type,
                        "payload": str(log.payload),
                        "is_malicious": False,
                        "reasoning": ""
                    }

                    try:
                        # The local SOC and chat share the same GPU/model instance.
                        async with queue.gpu_lock:
                            final_state = await soc_graph.ainvoke(initial_state)
                        verdict = AnalysisOutput.model_validate(final_state)
                    except Exception:
                        logger.exception("[SOC Worker] Analysis failed for log %s; left pending", log.id)
                        continue

                    log.investigated_at = datetime.now(timezone.utc)
                    log.is_compromised = verdict.is_malicious

                    updated_payload = dict(log.payload) if log.payload else {}
                    updated_payload["soc_reasoning"] = verdict.reasoning
                    log.payload = updated_payload

                    flag_modified(log, "payload")

                    logger.info(f"[SOC Worker] Log {log.id} analyzed -> is_malicious: {final_state['is_malicious']}")

                if logs_to_process:
                    await db.commit()

        except Exception as e:
            logger.error(f"[SOC Worker] Error in analyst loop: {e}")

        await asyncio.sleep(5)