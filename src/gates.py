"""
Admission gates for HyperClaw attention directives.

Each gate is a function: (output, task, criteria) -> GateResult
- Structural validation:    python-syntax, metta-syntax
- Execution:               exec-success
- Numerical validation:   math-result  (matches a safe arithmetic criterion)
- Task-consistency:        task-consistency (output structure matches task type)
- Minimal:                 nonempty, passthrough
"""
import ast
import math
import re
from src.helper import strip_code_fences


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

    depth = 0
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
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth < 0:
                    return GateResult(
                        False,
                        "MeTTa syntax invalid",
                        "Closing parenthesis appears before a matching opening parenthesis",
                    )

    if in_string:
        return GateResult(False, "MeTTa syntax invalid", "Unterminated string literal")

    if depth:
        return GateResult(False, "MeTTa syntax invalid", f"Unbalanced parentheses: depth {depth}")

    if not code.startswith("("):
        detail = f"MeTTa expression must start with '(' but got: '{code[:30]}'"
        return GateResult(False, "MeTTa syntax invalid", detail)

    return GateResult(True, "MeTTa syntax valid")


_NUMBER_LITERAL = r"-?(?:(?:\d{1,3}(?:,\d{3})+)|\d+)(?:\.\d+)?(?:[eE][+-]?\d+)?"
_FINAL_ANSWER_RE = re.compile(rf"^\s*FINAL_ANSWER\s*:\s*({_NUMBER_LITERAL})\s*$", re.MULTILINE)
_MAX_MATH_CRITERIA_LENGTH = 200
_MAX_MATH_AST_NODES = 64


def _parse_number(value: str) -> float | None:
    try:
        return float(value.replace(",", ""))
    except (AttributeError, ValueError):
        return None


def _evaluate_math_criterion(criteria: str) -> float:
    """Evaluate a bounded arithmetic expression without executing Python code."""
    expression = criteria.strip()
    if not expression:
        raise ValueError("Math criterion must be an arithmetic expression")
    if len(expression) > _MAX_MATH_CRITERIA_LENGTH:
        raise ValueError("Math criterion is too long")

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as error:
        raise ValueError("Math criterion is not a valid arithmetic expression") from error
    if sum(1 for _ in ast.walk(tree)) > _MAX_MATH_AST_NODES:
        raise ValueError("Math criterion is too complex")

    def evaluate(node: ast.AST) -> float:
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return float(node.value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = evaluate(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp):
            left, right = evaluate(node.left), evaluate(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            if isinstance(node.op, ast.FloorDiv):
                return left // right
            if isinstance(node.op, ast.Mod):
                return left % right
            if isinstance(node.op, ast.Pow):
                if abs(right) > 100:
                    raise ValueError("Exponent is too large")
                return left ** right
        raise ValueError("Math criterion contains an unsupported operation")

    try:
        value = evaluate(tree.body)
    except (ArithmeticError, OverflowError, ValueError) as error:
        raise ValueError(f"Could not evaluate math criterion: {error}") from error
    if not math.isfinite(value):
        raise ValueError("Math criterion must produce a finite number")
    return value


def gate_math_result(output: str, task: str, criteria: str) -> GateResult:
    """Validate a marked final answer against a safe arithmetic criterion."""
    try:
        expected = _evaluate_math_criterion(criteria)
    except ValueError as error:
        return GateResult(False, "Invalid math criterion", str(error))

    answers = _FINAL_ANSWER_RE.findall(output)
    answer_match = _FINAL_ANSWER_RE.search(output)
    if len(answers) != 1 or answer_match is None or output[answer_match.end():].strip():
        return GateResult(
            False,
            "Missing unambiguous final answer",
            "End the response with exactly one line: FINAL_ANSWER: <number>",
        )
    actual = _parse_number(answers[0])
    if actual is None or not math.isfinite(actual):
        return GateResult(False, "Invalid final answer", "FINAL_ANSWER must be a finite number")
    if not math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9):
        return GateResult(
            False,
            "Final answer does not match criterion",
            f"Expected {expected:g} from '{criteria}', got {actual:g}",
        )
    return GateResult(True, f"Final answer matches criterion: {expected:g}")


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
    "critique": (
        ("VERDICT:", "ISSUES:"),
        ("OVERALL:", "WEAKNESSES:", "SUGGESTIONS:"),
    ),
    "evaluate": (
        ("OVERALL:", "SCORE:", "STRENGTHS:", "WEAKNESSES:"),
        ("VERDICT:", "SCORE:", "STRENGTHS:", "WEAKNESSES:"),
    ),
    "revise": (("REVISED", "CHANGES:"),),
}
_NONEMPTY_TASKS = {"generate", "execute"}


def gate_task_consistency(output: str, task: str, criteria: str) -> GateResult:
    """
    Check that the output structure is consistent with the task type.

    Critique/Evaluate outputs must contain one complete expected section group.
    Revise outputs must contain both a revision and changes marker.
    Generate/Execute outputs only need to be non-empty.
    """
    if not output or not output.strip():
        return GateResult(False, "Empty output", "Module returned an empty string")

    if task in _NONEMPTY_TASKS:
        return GateResult(True, f"Task {task}: non-empty output accepted")

    marker_groups = _TASK_MARKER_GROUPS.get(task)
    if marker_groups is None:
        return GateResult(False, f"Unknown task '{task}'", "Use a supported directive task")

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
