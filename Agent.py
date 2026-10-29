# Chain Of Thought Prompting
import json
import os
import subprocess
import urllib.parse
from typing import Optional

from dotenv import load_dotenv
from groq import Groq
import requests
from pydantic import BaseModel, ConfigDict, Field, ValidationError

load_dotenv()
api_key = os.getenv("GROQ_API_KEY")

if not api_key:
    raise ValueError("GROQ_API_KEY not found in .env file")

client = Groq(api_key=api_key)


def run_command(cmd: str) -> str:
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30
        )
        output = (result.stdout + result.stderr).strip()
        return output if output else f"Command completed with exit code {result.returncode}"
    except Exception as error:
        return f"Tool execution error: {error}"


def get_weather(city: str) -> str:
    encoded_city = urllib.parse.quote(city.strip().lower())
    url = f"https://wttr.in/{encoded_city}?format=%C+%t"

    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            return f"The weather in {city} is {response.text.strip()}"
        return f"Weather service responded with status {response.status_code}."
    except requests.RequestException as error:
        return f"Could not connect to the weather service: {error}"


available_tools = {
    "get_weather": get_weather,
    "run_command": run_command
}


SYSTEM_PROMPT = """
You're an expert AI Assistant in resolving user queries using
a structured planning approach.

You work on START, PLAN, TOOL, OBSERVE, and OUTPUT steps.
You need to first PLAN what needs to be done. PLAN can contain
multiple steps. Once the task is complete, give an OUTPUT.

You can call a tool when required from the available tools.
For every tool call, wait for the OBSERVE step containing the
result returned by the tool.

Rules:
- Strictly follow the given JSON output format.
- Return only one JSON object per response.
- Only run one step at a time.
- The step must be START, PLAN, TOOL, OBSERVE, or OUTPUT.
- START acknowledges the user's input.
- PLAN gives a brief summary of the next action, not private
  chain-of-thought or hidden reasoning.
- TOOL requests a tool call.
- OBSERVE reports the tool's result.
- OUTPUT provides the final answer to the user.
- Do not invent tool results or claim a tool succeeded if it failed.

Output JSON Format:
{
  "step": "START | PLAN | TOOL | OBSERVE | OUTPUT",
  "content": "string or null",
  "tool": "string or null",
  "input": "string or null"
}

Available Tools:
- get_weather(city: str): Takes a city name and returns weather info.
- run_command(cmd: str): Executes a system command.

Example 1: Solving 2 + 3 * 5 / 10

{"step":"START","content":"You want to solve 2 + 3 * 5 / 10.","tool":null,"input":null}
{"step":"PLAN","content":"Apply the order of operations.","tool":null,"input":null}
{"step":"PLAN","content":"Calculate 3 * 5 = 15, then 15 / 10 = 1.5.","tool":null,"input":null}
{"step":"PLAN","content":"Add 2 + 1.5.","tool":null,"input":null}
{"step":"OUTPUT","content":"The answer is 3.5.","tool":null,"input":null}

Example 2: Weather in Delhi

{"step":"START","content":"You want the current weather in Delhi.","tool":null,"input":null}
{"step":"PLAN","content":"Retrieve weather information for Delhi.","tool":null,"input":null}
{"step":"TOOL","content":"Get the weather for Delhi.","tool":"get_weather","input":"Delhi"}
{"step":"OBSERVE","content":"The weather service returned cloudy, 20 C.","tool":"get_weather","input":"Delhi"}
{"step":"PLAN","content":"Summarize the returned weather information.","tool":null,"input":null}
{"step":"OUTPUT","content":"The current weather in Delhi is cloudy, 20 C.","tool":null,"input":null}
"""


class MyOutputFormat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step: str = Field(
        ...,
        description="START, PLAN, TOOL, OBSERVE, or OUTPUT"
    )
    content: Optional[str] = Field(default=None, description="Step content or null")
    tool: Optional[str] = Field(default=None, description="Tool name or null")
    input: Optional[str] = Field(default=None, description="Tool input or null")


def main():
    print("\nAI Agent started. Type 'exit' to stop.")

    message_history = [
        {"role": "system", "content": SYSTEM_PROMPT}
    ]

    max_steps_per_query = 15
    agent_step_schema = MyOutputFormat.model_json_schema()
    agent_step_schema["additionalProperties"] = False
    agent_step_schema["required"] = list(agent_step_schema.get("properties", {}).keys())

    while True:
        user_query = input("\nYou: ")

        if user_query.strip().lower() in ["exit", "quit"]:
            print("Agent stopped.")
            break

        if not user_query.strip():
            continue

        message_history.append({
            "role": "user",
            "content": user_query
        })

        step_count = 0

        while step_count < max_steps_per_query:
            step_count += 1

            try:
                response = client.chat.completions.create(
                    model="qwen/qwen3.8-27b",
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": "agent_step",
                            "strict": True,
                            "schema": agent_step_schema
                        }
                    },
                    messages=message_history,
                    max_tokens=500
                )
            except Exception as error:
                print(f"API request failed: {error}")
                break

            raw_result = response.choices[0].message.content

            if not raw_result:
                print("The model returned an empty response.")
                break

            try:
                parsed_result = MyOutputFormat.model_validate_json(raw_result)
            except ValidationError as error:
                print(f"JSON validation error: {error}")
                break

            message_history.append({
                "role": "assistant",
                "content": raw_result
            })

            if parsed_result.step == "START":
                print("🔥", parsed_result.content)
                continue

            if parsed_result.step == "PLAN":
                print("🧠", parsed_result.content)
                continue

            if parsed_result.step == "TOOL":
                tool_to_call = parsed_result.tool
                tool_input = parsed_result.input

                if tool_to_call not in available_tools:
                    observation = "Error: Tool is not available."
                elif not tool_input:
                    observation = "Error: Tool input is missing."
                else:
                    print(f"🛠️ {tool_to_call} ({tool_input})")
                    try:
                        observation = available_tools[tool_to_call](tool_input)
                    except Exception as error:
                        observation = f"Tool error: {error}"

                print(f"👀 Observation: {observation}")

                message_history.append({
                    "role": "user",
                    "content": json.dumps({
                        "step": "OBSERVE",
                        "content": str(observation),
                        "tool": tool_to_call,
                        "input": tool_input
                    })
                })
                continue

            if parsed_result.step == "OBSERVE":
                print("👀", parsed_result.content)
                continue

            if parsed_result.step == "OUTPUT":
                print("🤖", parsed_result.content)
                break

            print("Unknown step:", parsed_result.step)
            break
        else:
            print("⚠️ Step limit reached for this query.")


if __name__ == "__main__":
    main()
