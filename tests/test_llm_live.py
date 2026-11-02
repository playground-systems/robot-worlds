"""Opt-in live protocol smoke test; never runs merely because a key is set.

ROBOT_WORLDS_LIVE_API=1 python -m pytest tests/test_llm_live.py
Two bounded model calls exercise structured output and image + function input.
No robot tool is executed by this test.
"""

import io
import os

import pytest
from PIL import Image

from robot_worlds.llm import ResponsesClient


@pytest.mark.skipif(
    os.environ.get("ROBOT_WORLDS_LIVE_API") != "1", reason="Explicit live API opt-in required"
)
def test_live_astra_structured_generation_and_vision_tool_choice():
    client = ResponsesClient(
        timeout=90,
        max_seconds=180,
        max_calls=2,
        max_output_tokens=4096,
        max_total_output_tokens=8192,
    )
    schema = {
        "type": "object",
        "properties": {"ready": {"type": "boolean"}},
        "required": ["ready"],
        "additionalProperties": False,
    }
    assert client.complete_json("Return the requested JSON.", "Set ready to true.", schema) == {
        "ready": True
    }
    image_buffer = io.BytesIO()
    Image.new("RGB", (64, 64), (40, 110, 180)).save(image_buffer, format="PNG")
    image = image_buffer.getvalue()
    tools = [
        {
            "name": "look",
            "description": "Read the next camera observation.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        }
    ]
    action = client.choose_action(
        "Select look to inspect the sensor image.",
        {"task": "Inspect the image", "image": image},
        tools,
        [],
    )
    assert action == {"tool": "look", "arguments": {}}
    assert client.usage["calls"] == 2
    assert client.usage["total_tokens"] > 0
