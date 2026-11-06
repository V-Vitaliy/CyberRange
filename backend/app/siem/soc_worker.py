import asyncio
import logging
from datetime import datetime, timezone
from uuid import UUID

import httpx
import openai
from langchain_core.exceptions import ModelConnectionError
from sqlalchemy import or_, select

from app.agents.soc_agent import AnalysisOutput
from app.db.database import async_session
from app.db.models import GameSession, SecurityAuditLog
from app.siem.forensics import RULE_JUDGED_EVENTS, analyzer

logger = logging.getLogger(__name__)

SOC_BATCH_SIZE = 5
SOC_POLL_SECONDS = 5
SOC_MAX_BACKOFF_SECONDS = 60
SOC_RULE_BATCH_SIZE = 500


NETWORK_ERRORS = (
    ModelConnectionError,
    openai.APIConnectionError,
    httpx.TransportError,
    ConnectionError,
    TimeoutError,
)


def is_connection_error(error: BaseException | None) -> bool:
    """True when the model server could not be reached, including wrapped network errors.

    Only network failures count; other errors (e.g. file system) are reported per event.
    """
    seen = set()
    while error is not None and id(error) not in seen:
        if isinstance(error, NETWORK_ERRORS):
            return True
        seen.add(id(error))
        error = error.__cause__ or error.__context__
    return False


async def fetch_pending_logs() -> list[tuple[UUID, str, dict]]:
    """Read a batch of uninvestigated solo-lab events; the transaction ends before analysis."""
    async with async_session() as db:
        result = await db.execute(
            select(SecurityAuditLog.id, SecurityAuditLog.event_type, SecurityAuditLog.payload)
            .join(GameSession, SecurityAuditLog.lab_instance_id == GameSession.lab_instance_id)
            .where(
                SecurityAuditLog.investigated_at.is_(None),
                GameSession.is_solo.is_(True),
                SecurityAuditLog.event_type.notin_(RULE_JUDGED_EVENTS),
            )
            .order_by(SecurityAuditLog.timestamp)
            .limit(SOC_BATCH_SIZE)
        )
        return [tuple(row) for row in result.all()]


async def close_rule_judged_logs() -> int:
    """Give pending solo-lab events of RULE_JUDGED_EVENTS their rule verdict in one short transaction.

    No model call: a login burst cannot queue model work or delay the analysis of other events.
    """
    async with async_session() as db:
        result = await db.execute(
            select(SecurityAuditLog)
            .join(GameSession, SecurityAuditLog.lab_instance_id == GameSession.lab_instance_id)
            .where(
                SecurityAuditLog.investigated_at.is_(None),
                GameSession.is_solo.is_(True),
                SecurityAuditLog.event_type.in_(RULE_JUDGED_EVENTS),
                # A failed login whose count could not be taken stays pending: the rule cannot judge it.
                or_(
                    SecurityAuditLog.event_type != "LOGIN_FAILED",
                    SecurityAuditLog.payload["failed_usernames_from_source"].astext.isnot(None),
                ),
            )
            .order_by(SecurityAuditLog.timestamp)
            .limit(SOC_RULE_BATCH_SIZE)
            .with_for_update(of=SecurityAuditLog, skip_locked=True)
        )
        logs = result.scalars().all()
        now = datetime.now(timezone.utc)
        for log in logs:
            malicious = analyzer.predict_is_malicious(log.payload, log.event_type)
            log.investigated_at = now
            log.is_compromised = malicious
            verdict = "malicious" if malicious else "benign"
            log.payload = {**(log.payload or {}), "soc_reasoning": f"Rule-based verdict: {verdict} (no model call)."}
        await db.commit()
        return len(logs)


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
    backoff = SOC_POLL_SECONDS
    llm_down = False

    while True:
        outage = False
        try:
            closed = await close_rule_judged_logs()
            if closed:
                logger.info("[SOC Worker] %s rule-judged events closed without the model", closed)
        except Exception:
            # The model analysis below does not depend on this step.
            logger.exception("[SOC Worker] Rule-based verdicts failed; retrying on the next poll")
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
                except Exception as error:
                    if is_connection_error(error):
                        # Remaining events wait for the next attempt; one warning per outage.
                        outage = True
                        break
                    logger.exception("[SOC Worker] Analysis failed for log %s; left pending", log_id)
                    continue

                if llm_down:
                    llm_down = False
                    logger.info("[SOC Worker] LLM is reachable again; resuming analysis")
                if await save_verdict(log_id, verdict):
                    logger.info(f"[SOC Worker] Log {log_id} analyzed -> is_malicious: {verdict.is_malicious}")
                else:
                    logger.info(f"[SOC Worker] Log {log_id} was investigated meanwhile; verdict discarded")

        except Exception as e:
            logger.error(f"[SOC Worker] Error in analyst loop: {e}")

        if outage:
            if not llm_down:
                llm_down = True
                logger.warning(
                    "[SOC Worker] LLM is unreachable; events stay pending, retrying with backoff up to %ss",
                    SOC_MAX_BACKOFF_SECONDS,
                )
            delay, backoff = backoff, min(backoff * 2, SOC_MAX_BACKOFF_SECONDS)
        else:
            delay, backoff = SOC_POLL_SECONDS, SOC_POLL_SECONDS
        await asyncio.sleep(delay)
