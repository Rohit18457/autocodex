# Autonomous CLI Coding & Automation Agent (ReAct + Chain of Thought)
import inspect
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


# ==========================================
# TOOL DEFINITIONS
# ==========================================

def list_files(path: str = ".") -> str:
    """Lists files and directories inside a specified path."""
    try:
        target_path = os.path.abspath(path)
        if not os.path.exists(target_path):
            return f"Error: Path '{path}' does not exist."
        if not os.path.isdir(target_path):
            return f"Error: Path '{path}' is not a directory."

        entries = []
        for item in sorted(os.listdir(target_path)):
            full = os.path.join(target_path, item)
            kind = "[DIR] " if os.path.isdir(full) else "[FILE]"
            size = "" if os.path.isdir(full) else f" ({os.path.getsize(full)} bytes)"
            entries.append(f"{kind} {item}{size}")

        return "\n".join(entries) if entries else "(empty directory)"
    except Exception as error:
        return f"Error listing directory: {error}"


def read_file(path: str) -> str:
    """Reads and returns the contents of a file."""
    try:
        if not os.path.exists(path):
            return f"Error: File '{path}' does not exist."
        if not os.path.isfile(path):
            return f"Error: '{path}' is not a regular file."

        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return content if content else "(file is empty)"
    except Exception as error:
        return f"Error reading file '{path}': {error}"


def write_file(path: str, content: str) -> str:
    """Creates a new file or overwrites an existing file with given content."""
    try:
        parent_dir = os.path.dirname(path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully wrote {len(content)} characters to '{path}'."
    except Exception as error:
        return f"Error writing file '{path}': {error}"


def edit_file(path: str, find_text: str, replace_text: str) -> str:
    """Finds exact text in a file and replaces it."""
    try:
        if not os.path.exists(path):
            return f"Error: File '{path}' does not exist."

        with open(path, "r", encoding="utf-8") as f:
            file_data = f.read()

        if find_text not in file_data:
            return f"Error: Target text to replace was not found in '{path}'."

        new_data = file_data.replace(find_text, replace_text, 1)
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_data)
        return f"Successfully updated '{path}'."
    except Exception as error:
        return f"Error editing file '{path}': {error}"


def run_command(command: str) -> str:
    """Executes a terminal or shell command safely with timeout."""
    try:
        result = subprocess.run(
            command,
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
    """Fetches real-time weather information."""
    encoded_city = urllib.parse.quote(city.strip().lower())
    url = f"https://wttr.in/{encoded_city}?format=%C+%t"

    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            return f"The weather in {city} is {response.text.strip()}"
        return f"Weather service responded with status {response.status_code}."
    except requests.RequestException as error:
        return f"Could not connect to weather service: {error}"


available_tools = {
    "list_files": list_files,
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "run_command": run_command,
    "get_weather": get_weather,
}


def check_safety_permission(tool_name: str, kwargs: dict) -> tuple[bool, Optional[str]]:
    """Evaluates whether an action requires explicit user confirmation or is restricted."""
    if tool_name == "run_command":
        cmd = kwargs.get("command", "").strip()
        # Strictly forbid git push by agent
        if "git push" in cmd.lower():
            return False, "Security restriction: 'git push' commands are strictly reserved for the user and cannot be executed by the agent."

        print("\n⚠️  [SAFETY GUARD] The agent requests permission to run shell command:")
        print(f"   Command: {cmd}")
        try:
            choice = input("   Allow execution? (y/n) > ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            return False, "Execution aborted by user."

        if choice not in ["y", "yes"]:
            return False, "User denied permission to execute this command."

    elif tool_name == "write_file":
        path = kwargs.get("path", "")
        if os.path.exists(path):
            print(f"\n⚠️  [SAFETY GUARD] File '{path}' already exists.")
            print("   The agent wants to overwrite its contents.")
            try:
                choice = input("   Allow overwrite? (y/n) > ").strip().lower()
            except (KeyboardInterrupt, EOFError):
                return False, "Execution aborted by user."

            if choice not in ["y", "yes"]:
                return False, f"User denied permission to overwrite '{path}'."

    return True, None


def execute_tool(tool_name: str, raw_input: Optional[str]) -> str:
    """Dispatches tool calls handling JSON dictionaries or direct argument strings."""
    if tool_name not in available_tools:
        return f"Error: Tool '{tool_name}' not available. Allowed: {list(available_tools.keys())}"

    fn = available_tools[tool_name]
    kwargs = {}

    if raw_input:
        raw_input = raw_input.strip()
        try:
            parsed = json.loads(raw_input)
            if isinstance(parsed, dict):
                kwargs = parsed
            else:
                kwargs = {"arg": parsed}
        except json.JSONDecodeError:
            kwargs = {"arg": raw_input}

    sig = inspect.signature(fn)
    param_names = list(sig.parameters.keys())

    if not param_names:
        return fn()

    if "arg" in kwargs:
        kwargs = {param_names[0]: kwargs["arg"]}

    # Safety Guard check before running tool
    is_safe, error_message = check_safety_permission(tool_name, kwargs)
    if not is_safe:
        return f"Action blocked: {error_message}"

    try:
        return fn(**kwargs)
    except TypeError as te:
        return f"Argument error calling '{tool_name}': {te}. Provided: {raw_input}"
    except Exception as error:
        return f"Tool execution failed: {error}"


# ==========================================
# SYSTEM PROMPT
# ==========================================

SYSTEM_PROMPT = """
You are an expert autonomous CLI AI Coding & Automation Assistant.
You resolve user requests using a structured planning and tool-using approach.

You work on START, PLAN, TOOL, OBSERVE, and OUTPUT steps.
1. START acknowledges the user request.
2. PLAN outlines the next immediate step or strategy.
3. TOOL requests a tool execution.
4. OBSERVE is provided to you with the tool's result.
5. OUTPUT provides the final response to the user.

Rules:
- Strictly follow the given JSON format.
- Return only ONE JSON object per turn.
- Only run one step at a time.
- For tool calls with multiple arguments, supply them as a valid JSON string inside 'input'.
- Do not invent tool outputs; rely strictly on OBSERVE results.
- Human-in-the-Loop Safety Policy: For your safety, the system will pause and ask the user for confirmation (y/n) before running any shell commands (run_command) or overwriting existing files. Read-only actions (read_file, list_files) and creating brand new files run automatically.
- NEVER attempt to run 'git push'. Pushing code to remote repositories is strictly reserved for the human user.

Output JSON Format:
{
  "step": "START | PLAN | TOOL | OBSERVE | OUTPUT",
  "content": "string or null",
  "tool": "string or null",
  "input": "string or null"
}

Available Tools:
- list_files(path: str = "."): Lists all files and directories in a path.
  Input example: "{\\"path\\": \\".\\"}"
- read_file(path: str): Reads the entire content of a file.
  Input example: "{\\"path\\": \\"filename.py\\"}"
- write_file(path: str, content: str): Creates or overwrites a file.
  Input example: "{\\"path\\": \\"app.py\\", \\"content\\": \\"print('hello')\\"}"
- edit_file(path: str, find_text: str, replace_text: str): Finds exact text and replaces it.
  Input example: "{\\"path\\": \\"app.py\\", \\"find_text\\": \\"hello\\", \\"replace_text\\": \\"world\\"}"
- run_command(command: str): Executes a system shell command and captures terminal output.
  Input example: "{\\"command\\": \\"python app.py\\"}"
- get_weather(city: str): Returns weather info.
  Input example: "{\\"city\\": \\"Pune\\"}"

Example Workflow:
{"step":"START","content":"You want to create a python script that prints 'Hello World' and test run it.","tool":null,"input":null}
{"step":"PLAN","content":"Create hello.py using the write_file tool.","tool":null,"input":null}
{"step":"TOOL","content":"Creating hello.py","tool":"write_file","input":"{\\"path\\": \\"hello.py\\", \\"content\\": \\"print('Hello World')\\"}"}
{"step":"OBSERVE","content":"Successfully wrote 20 characters to 'hello.py'.","tool":"write_file","input":"{\\"path\\": \\"hello.py\\", \\"content\\": \\"...\\"}"}
{"step":"PLAN","content":"Run hello.py using run_command to verify the output.","tool":null,"input":null}
{"step":"TOOL","content":"Executing hello.py","tool":"run_command","input":"{\\"command\\": \\"python hello.py\\"}"}
{"step":"OBSERVE","content":"Hello World","tool":"run_command","input":"{\\"command\\": \\"python hello.py\\"}"}
{"step":"OUTPUT","content":"Created hello.py and executed it successfully. Terminal output: Hello World.","tool":null,"input":null}
"""


class MyOutputFormat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step: str = Field(
        ...,
        description="START, PLAN, TOOL, OBSERVE, or OUTPUT"
    )
    content: Optional[str] = Field(default=None, description="Step content or null")
    tool: Optional[str] = Field(default=None, description="Tool name or null")
    input: Optional[str] = Field(default=None, description="Tool inputs as JSON string or null")


def main():
    print("=" * 60)
    print("🤖 AutoCodeX — CLI AI Coding & Automation Assistant Started")
    print("Commands: Type your task or type 'exit' to quit.")
    print("=" * 60)

    message_history = [
        {"role": "system", "content": SYSTEM_PROMPT}
    ]

    max_steps_per_query = 15

    # Strict JSON Schema configuration for Groq API
    agent_step_schema = MyOutputFormat.model_json_schema()
    agent_step_schema["additionalProperties"] = False
    agent_step_schema["required"] = list(agent_step_schema.get("properties", {}).keys())

    while True:
        try:
            user_query = input("\nYou > ")
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
            break

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
                    max_tokens=600
                )
            except Exception as error:
                print(f"❌ API request failed: {error}")
                break

            raw_result = response.choices[0].message.content

            if not raw_result:
                print("❌ Empty response from model.")
                break

            try:
                parsed_result = MyOutputFormat.model_validate_json(raw_result)
            except ValidationError as error:
                print(f"❌ JSON validation error: {error}")
                break

            message_history.append({
                "role": "assistant",
                "content": raw_result
            })

            if parsed_result.step == "START":
                print(f"🔥 {parsed_result.content}")
                continue

            if parsed_result.step == "PLAN":
                print(f"🧠 {parsed_result.content}")
                continue

            if parsed_result.step == "TOOL":
                tool_to_call = parsed_result.tool
                tool_input = parsed_result.input

                print(f"🛠️ Tool: {tool_to_call}")
                if tool_input:
                    print(f"   Input: {tool_input}")

                observation = execute_tool(tool_to_call, tool_input)
                print(f"👀 Observation:\n{observation}\n")

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
                print(f"👀 {parsed_result.content}")
                continue

            if parsed_result.step == "OUTPUT":
                print(f"\n🤖 Answer:\n{parsed_result.content}")
                break

            print(f"Unknown step: {parsed_result.step}")
            break
        else:
            print("⚠️ Reached maximum step limit (15) for this task.")


if __name__ == "__main__":
    main()
