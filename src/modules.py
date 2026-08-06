"""Attention directive modules and their Python execution adapter."""

import json
import re
import subprocess
import tempfile
import os
import resource
from dataclasses import dataclass
from typing import Callable

from lib_llm_ext import callActiveProvider
from src.helper import strip_code_fences, strip_metta
from src.gates import run_gate

@dataclass
class ModuleResult:
    output: str
    success: bool
    error: str = ""

    def __repr__(self):
        status = "OK" if self.success else "ERROR"
        preview = self.output[:80].replace("\n", " ")
        return f"ModuleResult({status}: {preview})"

    def as_str(self) -> str:
        """Return the output string, prefixed with error info if failed."""
        if not self.success and self.error:
            return f"MODULE_ERROR: {self.error}\n{self.output}"
        return self.output

def _call_llm(prompt: str, max_tokens: int) -> ModuleResult:
    try:
        return ModuleResult(output=callActiveProvider(prompt, max_tokens=max_tokens), success=True)
    except Exception as e:
        return ModuleResult(output="", success=False, error=str(e))

_TASK_TEMPLATES: dict[str, tuple[str, str]] = {
    "generate":  ("You are a precise generator. Produce the requested output directly.",
                  "Generate the following:\n"),
    "critique":  ("You are a rigorous critic. Identify flaws, gaps, and inconsistencies.",
                  "Critique the following:\n"),
    "revise":    ("You are a careful revisor. Improve the content based on the feedback provided.",
                  "Revise the following, incorporating the feedback:\n"),
    "execute":   ("You are an execution engine. Run or apply the given instructions precisely.",
                  "Execute the following:\n"),
    "evaluate":  ("You are an objective evaluator. Assess quality, correctness, and completeness.",
                  "Evaluate the following. Format as OVERALL/SCORE/STRENGTHS/WEAKNESSES:\n"),
}

def _task_prompt(task: str, context: str, persona: str = "") -> str:
    """
    Build a prompt from the task template.
    persona: optional module-specific role sentence that replaces the default role.
    """
    role, instruction = _TASK_TEMPLATES.get(task, ("", f"TASK: {task}\n"))
    header = f"{persona or role}\n\n" if (persona or role) else ""
    return f"{header}{instruction}{context}"

_DEF_RE = re.compile(r"^\s*(class |def )", re.MULTILINE)

def _needs_test_invocation(code: str) -> bool:
    """True when code has class/def but no top-level executable call or print."""
    if not _DEF_RE.search(code):
        return False
    for line in code.splitlines():
        if line and not line[0].isspace() and not line.startswith(("class ", "def ", "#", "import ", "from ")):
            return False
    return True

def _general(context: str, task: str) -> ModuleResult:
    """General-purpose reasoning and generation using the loop's active LLM provider."""
    prompt = _task_prompt(task, context)
    if task == "generate":
        prompt = (
            "If the requested deliverable is Python code, return only the code. "
            "Do not add a greeting, Markdown fence, explanation, or claimed runtime output.\n\n"
            + prompt
        )
    result = _call_llm(prompt, max_tokens=6000)
    if not result.success:
        return result
    code = strip_code_fences(result.output)
    if task == "generate" and _needs_test_invocation(code):
        test = _call_llm(
            f"Append a short test invocation to the following Python code so running it produces visible output. "
            f"Return only the complete code with the test appended, no explanation.\n\n{code}",
            max_tokens=6000,
        )
        if test.success and test.output.strip():
            result.output = test.output
    return result

def _code_reviewer(context: str, task: str) -> ModuleResult:
    """Code review: returns VERDICT/ISSUES/SUGGESTION for the given code."""
    _formats = {
        "critique":  "VERDICT: PASS or FAIL\nISSUES: (list each issue, or 'None')\nSUGGESTION: (one concrete improvement, or 'None')",
        "evaluate":  "VERDICT: PASS or FAIL\nSCORE: (0-10)\nSTRENGTHS: (list)\nWEAKNESSES: (list)",
        "revise":    "REVISED_CODE:\n(full revised code here)\nCHANGES: (list of changes made)",
        "generate":  "(produce the requested code directly, no extra commentary)",
        "execute":   "(apply or run the instructions and report the result)",
    }
    fmt = _formats.get(task, _formats["critique"])
    prompt = (
        f"You are a precise code reviewer.\n\n"
        f"FORMAT YOUR RESPONSE AS:\n{fmt}\n\n"
        f"CODE:\n{context}"
    )
    return _call_llm(prompt, max_tokens=1500)

def _sandbox_limits():
    """Applied in the executor child process via preexec_fn."""
    # 5s CPU time — SIGXCPU on breach
    resource.setrlimit(resource.RLIMIT_CPU,   (5,   5))
    # 256 MB virtual memory
    resource.setrlimit(resource.RLIMIT_AS,    (256 * 1024 * 1024, 256 * 1024 * 1024))
    # 1 MB max file write
    resource.setrlimit(resource.RLIMIT_FSIZE, (1 * 1024 * 1024,   1 * 1024 * 1024))
    # 32 open file descriptors (limits socket creation)
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))


def _python_executor(context: str, _task: str) -> ModuleResult:
    """Runs Python in a resource-limited subprocess (not a security sandbox)."""
    code = strip_code_fences(context)

    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".py")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(code)
        except Exception:
            os.close(fd)
            raise
        os.chmod(tmp_path, 0o600)

        result = subprocess.run(
            ["python3", tmp_path],
            capture_output=True,
            text=True,
            timeout=10,
            preexec_fn=_sandbox_limits,
        )

        if result.returncode == 0:
            return ModuleResult(
                output=result.stdout or "(no output)",
                success=True,
            )
        else:
            return ModuleResult(
                output=result.stderr or result.stdout or "(no output)",
                success=False,
                error=f"Exit code {result.returncode}",
            )

    except subprocess.TimeoutExpired:
        return ModuleResult(output="", success=False, error="Execution timed out after 10 seconds")
    except Exception as e:
        return ModuleResult(output="", success=False, error=str(e))
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

def _researcher(context: str, task: str) -> ModuleResult:
    """Focused research synthesis from a clean user-task brief."""
    persona = (
        "You are a focused research subagent. Give a self-contained, concise, factual synthesis of the user's task.\n"
        "- Lead with the direct answer, then explain the key findings, reasoning, tradeoffs, and material caveats.\n"
        "- Choose the most useful structure for the question; do not follow a rigid template.\n"
        "- State uncertainty when it materially affects the answer.\n"
        "- Do not mention frame state, history, directives, tool calls, or search mechanics.\n"
        "- Do not invent sources or URLs."
    )
    return _call_llm(_task_prompt(task, context, persona=persona), max_tokens=3000)

def _critic(context: str, task: str) -> ModuleResult:
    """Identifies weaknesses and gaps — returns OVERALL/WEAKNESSES/SUGGESTIONS."""
    persona = (
        "You are a rigorous critic. Identify weaknesses, inconsistencies, and gaps.\n"
        "FORMAT YOUR RESPONSE AS:\n"
        "OVERALL: (one sentence assessment)\n"
        "WEAKNESSES: (list each weakness on a new line)\n"
        "SUGGESTIONS: (one concrete suggestion per weakness)"
    )
    return _call_llm(_task_prompt(task, context, persona=persona), max_tokens=2000)

_REGISTRY: dict[str, Callable[[str, str], ModuleResult]] = {
    "general":         _general,
    "code-reviewer":   _code_reviewer,
    "python-executor": _python_executor,
    "researcher":      _researcher,
    "critic":          _critic,
}

def invoke(name: str, context: str, task: str) -> ModuleResult:
    """Invoke a registered module by name. Returns a failed ModuleResult if the module is unknown."""
    module_fn = _REGISTRY.get(name)
    if module_fn is None:
        known = ", ".join(_REGISTRY.keys())
        return ModuleResult(
            output="",
            success=False,
            error=f"Unknown module '{name}'. Available: {known}",
        )
    return module_fn(context, task)


def list_modules_formatted() -> str:
    """Return registered module descriptions for the directive prompt."""
    return "\n".join(
        f"- {name}: {(fn.__doc__ or 'No description').strip().splitlines()[0]}"
        for name, fn in _REGISTRY.items()
    )

def _run_directive_cycle(
    target: str, context: str, task: str,
    gate: str, criteria: str, max_attempts: int
) -> str:
    """Core directive execution cycle: invoke module, run gate, retry on failure."""
    attempts    = 0
    last_reason = ""
    best_output = ""
    output      = ""
    while attempts < max_attempts:
        retry_context = context if attempts == 0 else (
            f"{context}\n\nPREVIOUS_ATTEMPT_FAILED: {last_reason}\nPlease fix and retry."
        )
        result      = invoke(target, retry_context, task)
        output      = result.as_str()
        if gate == "python-syntax":
            output = strip_code_fences(output)
        if not best_output and output.strip():
            best_output = output
        gate_result = run_gate(gate, output, task, criteria)
        attempts   += 1
        if gate_result.passed:
            return _directive_result(output, True, gate_result.reason, attempts)
        last_reason = gate_result.retry_feedback()
    failure_output = best_output or output
    return _directive_result(
        failure_output,
        False,
        f"failed after {attempts} attempts: {last_reason}",
        attempts,
    )


def _directive_result(output: str, admitted: bool, reason: str, attempts: int) -> str:
    """Serialize directive data as a safe, parseable MeTTa result expression."""
    return (
        "(DirectiveResult "
        f"(output {json.dumps(str(output), ensure_ascii=False)}) "
        f"(admitted {'True' if admitted else 'False'}) "
        f"(reason {json.dumps(str(reason), ensure_ascii=False)}) "
        f"(attempts {int(attempts)}))"
    )

def run_directive(
    target: str, context: str, task: str, gate: str,
    criteria: str, max_attempts: str
) -> str:
    """
    Execute a directive using context and retry policy prepared by attention.metta.
    Python owns module invocation and gate execution; MeTTa owns directive policy.
    Returns a serialized DirectiveResult expression for attention.metta to parse.
    """
    target = strip_metta(target)
    context = strip_metta(context)
    task = strip_metta(task)
    gate = strip_metta(gate)
    criteria = strip_metta(criteria)
    try:
        attempts = max(1, int(strip_metta(max_attempts)))
    except (ValueError, TypeError):
        attempts = 1
    return _run_directive_cycle(target, context, task, gate, criteria, max_attempts=attempts)
