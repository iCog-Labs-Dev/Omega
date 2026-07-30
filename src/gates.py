"""
Admission gates for HyperClaw attention directives.

Each gate is a function: (output, task, criteria) -> GateResult
- Structural validation:    python-syntax, metta-syntax
- Execution:               exec-success
- Numerical plausibility:  math-result  (range-aware via criteria)
- Task-consistency:        task-consistency (output structure matches task type)
- Minimal:                 nonempty, passthrough
"""
import ast
import re
from src.helper import strip_code_fences, strip_metta


class GateResult:
    def __init__(self, passed: bool, reason: str, detail: str = ""):
        self.passed = passed
        self.reason = reason
        # detail is the raw error info appended to the retry prompt
        self.detail = detail or reason

    def __repr__(self):
        status = "PASSED" if self.passed else "FAILED"
        return f"GateResult({status}: {self.reason})"

    def retry_feedback(self) -> str:
        """What gets appended to the retry prompt when gate fails."""
        return f"GATE_FAILED: {self.detail}"


def gate_python_syntax(output: str, task: str, criteria: str) -> GateResult:
    """Check Python code syntax using ast.parse."""
    code = strip_code_fences(output)
    if not code.strip():
        return GateResult(False, "Empty Python output", "Module returned empty code — generate actual Python code")
    try:
        ast.parse(code)
        return GateResult(True, "Python syntax valid")
    except SyntaxError as e:
        detail = f"SyntaxError at line {e.lineno}, col {e.offset}: {e.msg}"
        return GateResult(False, "Python syntax invalid", detail)
    except Exception as e:
        return GateResult(False, "Python parse error", str(e))


def gate_metta_syntax(output: str, task: str, criteria: str) -> GateResult:
    """Check basic MeTTa structure: non-empty, starts with '(', balanced parentheses outside strings."""
    code = strip_code_fences(output).strip()
    if not code:
        return GateResult(False, "Empty MeTTa output", "Output is empty")

    open_count = 0
    close_count = 0
    in_string = False
    escaped = False
    for ch in code:
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if not in_string:
            if ch == "(":
                open_count += 1
            elif ch == ")":
                close_count += 1

    if open_count != close_count:
        detail = (
            f"Unbalanced parentheses: {open_count} opening, "
            f"{close_count} closing. "
            f"Difference: {abs(open_count - close_count)}"
        )
        return GateResult(False, "MeTTa syntax invalid", detail)

    if not code.startswith("("):
        detail = f"MeTTa expression must start with '(' but got: '{code[:30]}'"
        return GateResult(False, "MeTTa syntax invalid", detail)

    return GateResult(True, "MeTTa syntax valid")


def gate_math_result(output: str, task: str, criteria: str) -> GateResult:
    """
    Check that the output contains a standalone numerical result and that it
    falls within any range specified in the criteria string.

    Criteria examples (all optional):
      "between 0 and 1"   -> 0 <= value <= 1
      "> 0"               -> value > 0
      ">= 0.5"            -> value >= 0.5
      "< 100"             -> value < 100
      "<= 10"             -> value <= 10
    If no range is specified in criteria, only presence is checked.
    """
    # Require number to be a standalone token — avoids matching dates or version strings.
    pattern = r"(?<![\w-])-?\d[\d,.]*(?![\w-])"
    matches = re.findall(pattern, output.strip())
    if not matches:
        return GateResult(
            False,
            "No numerical result found",
            f"Expected a number in the response but got: '{output[:100]}'"
        )

    # Parse the first number found
    try:
        value = float(matches[0].replace(",", ""))
    except ValueError:
        return GateResult(True, f"Numerical result found: {matches[0]}")

    # Range check from criteria
    if criteria and criteria.strip():
        c = criteria.strip().lower()

        # "between X and Y"
        m = re.search(r"between\s+([-\d.]+)\s+and\s+([-\d.]+)", c)
        if m:
            lo, hi = float(m.group(1)), float(m.group(2))
            if not (lo <= value <= hi):
                return GateResult(
                    False,
                    f"Value {value} outside range [{lo}, {hi}]",
                    f"Expected a value between {lo} and {hi} but got {value}"
                )
            return GateResult(True, f"Numerical result {value} within [{lo}, {hi}]")

        # ">= X", "> X", "<= X", "< X"
        for op_str, op_re in [(">=", r">=\s*([-\d.]+)"), (">", r"(?<!<)(?<!>)>\s*([-\d.]+)"),
                               ("<=", r"<=\s*([-\d.]+)"), ("<", r"(?<!<)(?<!>)<\s*([-\d.]+)")]:
            m = re.search(op_re, c)
            if m:
                bound = float(m.group(1))
                ops = {">=": value >= bound, ">": value > bound,
                       "<=": value <= bound, "<": value < bound}
                if not ops[op_str]:
                    return GateResult(
                        False,
                        f"Value {value} does not satisfy {op_str} {bound}",
                        f"Expected value {op_str} {bound} but got {value}"
                    )
                return GateResult(True, f"Numerical result {value} satisfies {op_str} {bound}")

    return GateResult(True, f"Numerical result found: {matches[0]}")


def gate_nonempty(output: str, task: str, criteria: str) -> GateResult:
    """Minimal gate — just checks the response is non-empty."""
    if output and output.strip():
        return GateResult(True, "Non-empty response")
    return GateResult(False, "Empty response", "Module returned an empty string")


def gate_exec_success(output: str, task: str, criteria: str) -> GateResult:
    """Gate for execution output — fails if empty or subprocess errored."""
    if not output or not output.strip():
        return GateResult(False, "Empty execution output", "Executor returned no output")
    if output.startswith("MODULE_ERROR:"):
        return GateResult(False, "Execution failed", output[:300])
    return GateResult(True, "Execution succeeded")


def gate_research_quality(output: str, task: str, criteria: str) -> GateResult:
    """Reject empty, trivial, or frame-contaminated research output."""
    if not output or not output.strip():
        return GateResult(False, "Empty research output", "Researcher returned an empty response")
    if len(output.strip()) < 120:
        return GateResult(False, "Research output too short", "Provide a substantive research synthesis")
    forbidden = ("human_message:", "history-summary", "directiveadmitted", "directiverejected", "contextprojection")
    lowered = output.lower()
    leaked = [marker for marker in forbidden if marker in lowered]
    if leaked:
        return GateResult(False, "Research output contains frame metadata", f"Remove frame metadata: {', '.join(leaked)}")
    return GateResult(True, "Research output is substantive and free of frame metadata")


def gate_passthrough(output: str, task: str, criteria: str) -> GateResult:
    """Always admits. Used when no gate is needed."""
    return GateResult(True, "Passthrough gate — always admits")


# Each task may use one of several complete marker groups. This supports the
# code-reviewer and critic module formats without admitting a lone heading.
_TASK_MARKER_GROUPS: dict[str, tuple[tuple[str, ...], ...]] = {
    "Critique": (
        ("VERDICT:", "ISSUES:"),
        ("OVERALL:", "WEAKNESSES:", "SUGGESTIONS:"),
    ),
    "Evaluate": (
        ("OVERALL:", "SCORE:", "STRENGTHS:", "WEAKNESSES:"),
        ("VERDICT:", "SCORE:", "STRENGTHS:", "WEAKNESSES:"),
    ),
    "Revise": (("REVISED", "CHANGES:"),),
}


def gate_task_consistency(output: str, task: str, criteria: str) -> GateResult:
    """
    Check that the output structure is consistent with the task type.

    Critique/Evaluate outputs must contain one complete expected section group.
    Revise outputs must contain both a revision and changes marker.
    Generate/Execute outputs only need to be non-empty.
    """
    if not output or not output.strip():
        return GateResult(False, "Empty output", "Module returned an empty string")

    marker_groups = _TASK_MARKER_GROUPS.get(task, ())
    if not marker_groups:
        # Generate and Execute: non-empty is sufficient
        return GateResult(True, f"Task {task}: non-empty output accepted")

    upper = output.upper()
    for group in marker_groups:
        if all(marker in upper for marker in group):
            return GateResult(True, f"Task {task}: found expected markers {list(group)}")

    return GateResult(
        False,
        f"Task {task}: output missing expected structure",
        f"Expected one complete marker group {list(marker_groups)} for a {task} task. "
        f"Got: '{output[:150]}'"
    )


_GATES = {
    "python-syntax":      gate_python_syntax,
    "metta-syntax":       gate_metta_syntax,
    "math-result":        gate_math_result,
    "nonempty":           gate_nonempty,
    "exec-success":       gate_exec_success,
    "research-quality":   gate_research_quality,
    "task-consistency":   gate_task_consistency,
    "passthrough":        gate_passthrough,
}


def run_gate(gate_name: str, output: str, task: str, criteria: str = "") -> GateResult:
    """
    Dispatch to the named gate.
    Unknown gate names return a failed GateResult so the caller knows
    the intended gate never ran — prevents silent wrong-gate admission.
    """
    if str(output).lstrip().startswith("MODULE_ERROR:"):
        return GateResult(False, "Module execution failed", str(output)[:300])

    fn = _GATES.get(gate_name)
    if fn is None:
        known = ", ".join(_GATES.keys())
        return GateResult(
            False,
            f"Unknown gate '{gate_name}'",
            f"Unknown gate '{gate_name}'. Available: {known}"
        )
    return fn(output, task, criteria)


def run_gate_from_metta(gate: str, output: str, task: str, criteria: str) -> str:
    """Bridge: run a gate and return 'passed|||reason|||detail' string."""
    result = run_gate(
        strip_metta(gate),
        strip_metta(output),
        strip_metta(task),
        strip_metta(criteria),
    )
    passed = "True" if result.passed else "False"
    return f"{passed}|||{result.reason}|||{result.detail}"
