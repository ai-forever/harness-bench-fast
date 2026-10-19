import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('proxy', Path(__file__).parents[1] / 'scripts/hbf_protocol_proxy.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


def test_codex_text_results_normalized_without_changing_calls():
    body = {'input': [{'type': 'function_call', 'name': 'exec_command', 'arguments': '{}'},
                      {'type': 'function_call_output', 'call_id': 'abc', 'output': [
                          {'type': 'input_text', 'text': 'a'}, {'type': 'input_text', 'text': 'b'}]}]}
    assert p.normalize_responses(body)['input'][1] == {'type': 'function_call_output', 'call_id': 'abc', 'output': 'a\nb'}
    assert body['input'][0]['arguments'] == '{}'


def test_reject_nontext_instead_of_silently_losing_content():
    with pytest.raises(ValueError):
        p.text_blocks([{'type': 'image', 'source': {}}])


def test_claude_tool_roundtrip_and_usage():
    response = p.chat_to_message({'choices': [{'message': {'content': None, 'tool_calls': [
        {'id': 'a', 'function': {'name': 'Bash', 'arguments': '{"command":"pwd"}'}}]},
        'finish_reason': 'tool_calls'}], 'usage': {'prompt_tokens': 20, 'completion_tokens': 7}}, 'test')
    assert response['stop_reason'] == 'tool_use'
    request = p.messages_to_chat({'model': 'test', 'system': [{'type': 'text', 'text': 'sys'}], 'messages': [
        {'role': 'assistant', 'content': response['content']},
        {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'a', 'content': '/workspace'},
                                     {'type': 'text', 'text': 'continue'}]}]})
    assert [x['role'] for x in request['messages']] == ['system', 'assistant', 'tool', 'user']
    assert request['messages'][2]['tool_call_id'] == 'a'
    events = list(p.message_events(response))
    assert events[0]['message']['usage'] == {'input_tokens': 20, 'output_tokens': 0}
    assert events[2]['delta']['partial_json'] == '{"command": "pwd"}'
    assert events[-2]['usage']['output_tokens'] == 7
    assert events[-1]['type'] == 'message_stop'


def test_unknown_blocks_and_thinking_fail_closed():
    for body in [dict(messages=[{'role': 'user', 'content': [{'type': 'image'}]}]),
                 dict(messages=[], thinking={'type': 'enabled', 'budget_tokens': 100})]:
        with pytest.raises(ValueError):
            p.messages_to_chat(dict(model='test', **body))


def test_reasoning_summary_preserved_without_duplicates():
    item = {'type': 'reasoning', 'summary': [{'type': 'summary_text', 'text': 'reason'}],
            'content': [{'type': 'reasoning_text', 'text': 'reason'}]}
    assert p.normalize_responses({'input': [item]})['input'][0]['content'] == [
        {'type': 'reasoning_text', 'text': 'reason'}]
    assert item['summary'] == []
    item = {'type': 'reasoning', 'summary': [{'type': 'summary_text', 'text': 'kept'}]}
    p.normalize_responses({'input': [item]})
    assert item['content'][0]['text'] == 'kept'
