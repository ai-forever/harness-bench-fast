"""Usage belongs to an API response, not to each metadata representation."""

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from harness_bench.agent_stats import AgentRunStatsCollector, agent_stats_from_result


def _tokens(stats):
    return {key: value for key, value in stats.items() if key.endswith("_tokens")}


def _response(message, llm_output=None):
    return LLMResult(generations=[[ChatGeneration(message=message)]], llm_output=llm_output)


@pytest.mark.parametrize(
    ("provider_key", "raw_usage"),
    [
        (
            "token_usage",
            {
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "total_tokens": 110,
                "prompt_tokens_details": {"cached_tokens": 80},
            },
        ),
        (
            "token_usage",
            {
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "total_tokens": 110,
                "precached_prompt_tokens": 80,
            },
        ),
        (
            "usage",
            {
                "input_tokens": 20,
                "cache_read_input_tokens": 70,
                "cache_creation_input_tokens": 10,
                "output_tokens": 10,
            },
        ),
    ],
    ids=["openai", "gigachat", "anthropic"],
)
def test_response_usage_is_counted_once_across_provider_representations(provider_key, raw_usage):
    raw_usage = {**raw_usage, "cost": 0.025}
    message = AIMessage(
        content="done",
        usage_metadata={"input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
        response_metadata={provider_key: raw_usage},
    )
    response = _response(message, {provider_key: raw_usage})
    collector = AgentRunStatsCollector()
    collector.as_callback().on_llm_end(response)

    expected = {
        "agent_input_tokens": 100,
        "agent_output_tokens": 10,
        "agent_total_tokens": 110,
    }
    assert _tokens(collector.stats) == expected
    assert _tokens(agent_stats_from_result({"messages": [message]})) == expected
    assert _tokens(collector.merged({"messages": [message]})) == expected
    assert collector.extra() == {"agent_peak_input_tokens": 100, "agent_cost_usd": 0.025}


def test_equal_usage_from_independent_calls_is_not_deduplicated():
    message = AIMessage(
        content="done",
        usage_metadata={"input_tokens": 10, "output_tokens": 1, "total_tokens": 11},
        response_metadata={
            "token_usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11}
        },
    )
    collector = AgentRunStatsCollector()
    callback = collector.as_callback()
    response = _response(message, {"token_usage": message.response_metadata["token_usage"]})
    callback.on_llm_end(response)
    callback.on_llm_end(response)

    expected = {"agent_input_tokens": 20, "agent_output_tokens": 2, "agent_total_tokens": 22}
    assert _tokens(collector.stats) == expected
    assert _tokens(agent_stats_from_result({"messages": [message, message]})) == expected
    assert collector.stats["agent_llm_calls"] == 2


def test_result_dict_prefers_standardized_message_usage():
    result = {
        "messages": [
            {
                "role": "assistant",
                "usage_metadata": {"input_tokens": 10, "output_tokens": 1, "total_tokens": 11},
                "response_metadata": {
                    "token_usage": {"prompt_tokens": 100, "completion_tokens": 10},
                    "usage": {"input_tokens": 1000, "output_tokens": 100},
                },
            }
        ]
    }
    assert _tokens(agent_stats_from_result(result)) == {
        "agent_input_tokens": 10,
        "agent_output_tokens": 1,
        "agent_total_tokens": 11,
    }


@pytest.mark.parametrize("llm_output", [None, {}, {"token_usage": {"cost": 0.01}}])
def test_streaming_or_missing_aggregate_uses_message_usage(llm_output):
    message = AIMessage(
        content="done",
        usage_metadata={"input_tokens": 10, "output_tokens": 1, "total_tokens": 11},
        response_metadata={"usage": {"input_tokens": 10, "output_tokens": 1}},
    )
    collector = AgentRunStatsCollector()
    collector.as_callback().on_llm_end(_response(message, llm_output))
    assert _tokens(collector.stats) == {
        "agent_input_tokens": 10,
        "agent_output_tokens": 1,
        "agent_total_tokens": 11,
    }


@pytest.mark.parametrize("key", ["token_usage", "usage", "usage_metadata"])
def test_response_metadata_fallback_without_standardized_usage(key):
    message = AIMessage(
        content="done", response_metadata={key: {"input_tokens": 10, "output_tokens": 1}}
    )
    collector = AgentRunStatsCollector()
    collector.as_callback().on_llm_end(_response(message))
    expected = {"agent_input_tokens": 10, "agent_output_tokens": 1, "agent_total_tokens": 11}
    assert _tokens(collector.stats) == expected
    assert _tokens(agent_stats_from_result({"messages": [message.model_dump()]})) == expected


@pytest.mark.parametrize("has_aggregate", [False, True])
def test_batch_counts_each_prompt_once_not_each_candidate(has_aggregate):
    message = AIMessage(
        content="done",
        usage_metadata={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
    )
    # Provider usage covers both choices; the second prompt happens to cost the same.
    generations = [
        [ChatGeneration(message=message), ChatGeneration(message=message)],
        [ChatGeneration(message=message), ChatGeneration(message=message)],
    ]
    aggregate = {"prompt_tokens": 20, "completion_tokens": 4, "total_tokens": 24}
    response = LLMResult(
        generations=generations,
        llm_output={"token_usage": aggregate, "usage": aggregate} if has_aggregate else None,
    )
    collector = AgentRunStatsCollector()
    collector.as_callback().on_llm_end(response)
    assert _tokens(collector.stats) == {
        "agent_input_tokens": 20,
        "agent_output_tokens": 4,
        "agent_total_tokens": 24,
    }


def test_aggregate_usage_has_precedence_over_per_message_copies():
    message = AIMessage(
        content="done",
        usage_metadata={"input_tokens": 10, "output_tokens": 1, "total_tokens": 11},
    )
    collector = AgentRunStatsCollector()
    collector.as_callback().on_llm_end(
        _response(message, {"token_usage": {"prompt_tokens": 20, "completion_tokens": 3}})
    )
    assert _tokens(collector.stats) == {
        "agent_input_tokens": 20,
        "agent_output_tokens": 3,
        "agent_total_tokens": 23,
    }


def test_zero_usage_is_recorded_and_does_not_fall_through_to_other_aliases():
    message = {
        "role": "assistant",
        "usage_metadata": {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "prompt_tokens": 99,
            "completion_tokens": 99,
        },
        "response_metadata": {"usage": {"input_tokens": 100, "output_tokens": 100}},
    }
    assert _tokens(agent_stats_from_result({"messages": [message]})) == {
        "agent_input_tokens": 0,
        "agent_output_tokens": 0,
        "agent_total_tokens": 0,
    }


def test_missing_usage_does_not_create_zero_token_measurement():
    collector = AgentRunStatsCollector()
    collector.as_callback().on_llm_end(_response(AIMessage(content="done")))
    assert _tokens(collector.stats) == {}
    assert _tokens(agent_stats_from_result({"messages": [{"role": "assistant"}]})) == {}
