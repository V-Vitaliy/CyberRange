import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessageChunk

from app.core import llm_engine
from app.core.tools import send_email
from app.services.email_service import EmailService


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.setattr(llm_engine.settings, "SMTP_HOST", "")
    audit = AsyncMock()
    monkeypatch.setattr(llm_engine, "log_security_event", audit)
    delivery = Mock(return_value=True)
    monkeypatch.setattr(EmailService, "send_real_email", delivery)

    async def in_thread(function, *args):
        return function(*args)

    monkeypatch.setattr(llm_engine.asyncio, "to_thread", in_thread)
    return audit, delivery


async def run_stream(chunks):
    async def stream(messages):
        for chunk in chunks:
            yield chunk
    model = Mock()
    model.bind_tools.return_value = SimpleNamespace(astream=stream)
    output = ''.join([part async for part in llm_engine.generate_unified_stream(
        model, 'system', 'question', object(), uuid4())])
    model.bind_tools.assert_called_once_with([send_email])
    return output


def tool_chunk(args, name='send_email', index=0, call_id='call1'):
    return AIMessageChunk(content='', tool_call_chunks=[{'name': name, 'args': args, 'id': call_id, 'index': index}])


@pytest.mark.asyncio
async def test_plain_answer_keeps_streaming_without_email(harness):
    audit, delivery = harness
    assert await run_stream([AIMessageChunk(content='Hello '), AIMessageChunk(content='student')]) == 'Hello student'
    delivery.assert_not_called()
    audit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('chunks', [
    [],
    [AIMessageChunk(content='', additional_kwargs={'reasoning_content': 'reasoning only'})],
    [AIMessageChunk(content='', response_metadata={'finish_reason': 'length'})],
    [AIMessageChunk(content='partial answer', response_metadata={'finish_reason': 'length'})],
    [AIMessageChunk(content='', response_metadata={'finish_reason': 'length'}, tool_call_chunks=[{
        'name': 'send_email', 'id': 'call1', 'index': 0,
        'args': '{"to_address":"student@example.invalid","subject":"Test","body":"Hello"}',
    }])],
])
async def test_empty_or_truncated_model_output_fails_without_tool_execution(harness, chunks):
    with pytest.raises(RuntimeError, match='Model (returned no answer|output limit reached)'):
        await run_stream(chunks)
    harness[0].assert_not_awaited()
    harness[1].assert_not_called()


@pytest.mark.asyncio
async def test_split_tool_arguments_execute_once_and_report_simulation(harness):
    audit, delivery = harness
    output = await run_stream([
        tool_chunk('{"to_address":"student@example.invalid",'),
        tool_chunk('"subject":"Test","body":"Hello"}', name=None, call_id=None),
    ])
    delivery.assert_called_once_with('student@example.invalid', 'Test', 'Hello')
    assert 'No email was sent' in output
    assert audit.call_args.kwargs['event_type'] == 'AI_EMAIL_SIMULATED'
    assert audit.call_args.kwargs['payload']['delivery'] == 'simulated'


@pytest.mark.asyncio
@pytest.mark.parametrize('args,name', [
    ('{"to_address":"x"', 'send_email'),
    ('{}', 'send_email'),
    ('[]', 'send_email'),
    ('{"to_address":42,"subject":"x","body":"x"}', 'send_email'),
    ('{"to_address":"x","subject":"x","body":"x","extra":true}', 'send_email'),
    ('{"to_address":"x","subject":"x","body":"x"}', 'unknown'),
])
async def test_invalid_tool_never_sends(harness, args, name):
    audit, delivery = harness
    assert 'invalid tool or arguments' in await run_stream([tool_chunk(args, name)])
    delivery.assert_not_called()
    assert audit.call_args.kwargs['event_type'] == 'AI_TOOL_FAILED'


@pytest.mark.asyncio
async def test_multiple_tool_calls_are_not_mixed(harness):
    _, delivery = harness
    chunks = [tool_chunk(json.dumps(dict(to_address=f'{n}@example.invalid', subject='Test', body=str(n))), index=n, call_id=f'call{n}') for n in range(2)]
    await run_stream(chunks)
    assert delivery.call_count == 2
    assert delivery.call_args_list[0].args[0] == '0@example.invalid'
    assert delivery.call_args_list[1].args[0] == '1@example.invalid'


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', [False, RuntimeError('SMTP unavailable')])
async def test_failed_delivery_is_not_logged_as_sent(harness, outcome):
    audit, delivery = harness
    if isinstance(outcome, Exception):
        delivery.side_effect = outcome
    else:
        delivery.return_value = outcome
    output = await run_stream([tool_chunk(json.dumps(dict(to_address='test@example.invalid', subject='Test', body='flag{test}')))])
    assert 'failed' in output
    assert audit.call_args.kwargs['event_type'] == 'AI_EMAIL_FAILED'
    assert audit.call_args.kwargs['payload']['exfiltration_attempt'] is True


@pytest.mark.asyncio
async def test_simulated_exfiltration_remains_visible_to_siem(harness):
    audit, _ = harness
    await run_stream([tool_chunk(json.dumps(dict(to_address='test@example.invalid', subject='Test', body='flag{test}')))])
    assert audit.call_args.kwargs['event_type'] == 'AI_DATA_EXFILTRATION'
    assert audit.call_args.kwargs['payload']['delivery'] == 'simulated'


def test_email_mock_cannot_connect_to_smtp(monkeypatch):
    from app.services import email_service
    monkeypatch.setattr(email_service.settings, 'SMTP_HOST', '')
    smtp = Mock(side_effect=AssertionError('Real SMTP must not be contacted'))
    monkeypatch.setattr(email_service.smtplib, 'SMTP', smtp)
    assert EmailService.send_real_email('test@example.invalid', 'Test', 'test') is True
    smtp.assert_not_called()


def test_real_groq_adapter_binds_email_schema_without_network(monkeypatch):
    monkeypatch.setattr(llm_engine.settings, 'GROQ_API_KEY', 'unused-test-key')
    model = llm_engine.init_groq()
    bound = model.bind_tools([send_email])
    schema = bound.kwargs['tools'][0]['function']
    assert schema['name'] == 'send_email'
    assert set(schema['parameters']['required']) == {'to_address', 'subject', 'body'}
    assert not model.model_kwargs.get('tools')


@pytest.mark.asyncio
async def test_chat_sse_records_simulated_tool_result_and_history(harness, monkeypatch):
    from app.services import rag_service
    from app.db.repository import ChatRepository
    from app.core.queue_manager import LLMQueueManager

    audit, delivery = harness
    lab_id = uuid4()
    session = SimpleNamespace(lab_instance_id=lab_id, use_reranker=False, system_prompt='test')
    monkeypatch.setattr(rag_service, 'get_authorized_session', AsyncMock(return_value=session))
    monkeypatch.setattr(ChatRepository, 'get_or_create_thread', AsyncMock())
    history = AsyncMock()
    monkeypatch.setattr(ChatRepository, 'append_messages', history)

    async def stream(messages):
        yield tool_chunk(json.dumps(dict(to_address='test@example.invalid', subject='Test', body='test')))

    model = Mock()
    model.bind_tools.return_value = SimpleNamespace(astream=stream)
    user = dict(id=str(uuid4()), sub='student', role='red_team', lab_instance_id=str(lab_id))
    store = SimpleNamespace(search_documents=Mock(return_value={'documents': [[]]}))
    parts = [part async for part in rag_service.generate_chat_response_sse(object(), uuid4(), uuid4(), 'Send a test email', LLMQueueManager(), store, user, model)]
    assert parts[-1] == 'data: [DONE]\n\n'
    assert 'No email was sent' in history.call_args.kwargs['ai_response']
    assert audit.call_args.kwargs['lab_instance_id'] == str(lab_id)
    delivery.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('finish_reason', ['stop', 'length'])
async def test_empty_chat_emits_sse_error_without_saving_success(harness, monkeypatch, finish_reason):
    from app.services import rag_service
    from app.db.repository import ChatRepository
    from app.core.queue_manager import LLMQueueManager

    lab_id = uuid4()
    session = SimpleNamespace(lab_instance_id=lab_id, use_reranker=False, system_prompt='test')
    monkeypatch.setattr(rag_service, 'get_authorized_session', AsyncMock(return_value=session))
    monkeypatch.setattr(ChatRepository, 'get_or_create_thread', AsyncMock())
    history = AsyncMock()
    monkeypatch.setattr(ChatRepository, 'append_messages', history)

    async def stream(messages):
        yield AIMessageChunk(content='', response_metadata={'finish_reason': finish_reason})

    model = Mock()
    model.bind_tools.return_value = SimpleNamespace(astream=stream)
    user = dict(id=str(uuid4()), sub='student', role='red_team', lab_instance_id=str(lab_id))
    store = SimpleNamespace(search_documents=Mock(return_value={'documents': [[]]}))
    parts = [part async for part in rag_service.generate_chat_response_sse(
        object(), uuid4(), uuid4(), 'Question', LLMQueueManager(), store, user, model)]
    assert parts[-1] == 'data: [DONE]\n\n'
    assert 'error' in json.loads(parts[0][6:])
    history.assert_not_awaited()
    harness[1].assert_not_called()


@pytest.mark.asyncio
async def test_model_without_tools_retains_text_chat(harness):
    async def stream(messages):
        yield AIMessageChunk(content='Local answer')

    model = Mock()
    model.bind_tools.side_effect = NotImplementedError()
    model.astream = stream
    output = ''.join([part async for part in llm_engine.generate_unified_stream(model, 'system', 'question', object(), uuid4())])
    assert output == 'Local answer'
    harness[1].assert_not_called()
