"""Posts reach a chat only while someone there is waiting for an answer.

The agent keeps working between messages - research, notes, memory queries -
and nothing in core stops it from posting the results into the group. Neither
does core's own fallback notice when the model runs out of tokens on an idle
turn. Both read as spam. So the channel lets a post through only while a
message in that chat is still waiting for its answer, and turns the rest back
to the model as a refusal; the work itself goes on untouched.

Each message is answered on its own, so two questions sent close together
both get through, and each allows posts in two turns, so a short "looking it
up" note does not use up the answer. The agent asks for a new message once
per turn, which is how the gate tells turns apart:

    gate = ReplyGate()
    gate.next_turn()                 # the agent asked for its next message
    gate.message_arrived(chat_id)    # ... and got one from chat_id
    gate.check(chat_id)              # None: the reply may go out
    gate.delivered(chat_id)          # a note went out; more pieces this turn pass
    gate.next_turn()
    gate.check(chat_id)              # None: the answer may still follow
    gate.delivered(chat_id)
    gate.next_turn()
    gate.check(chat_id)              # Refusal: nobody is waiting any more
"""
import time
from dataclasses import dataclass

# A message nobody answered stops opening the gate after this long, so a reply
# the model forgot cannot license a research post an hour later.
WAITING_FOR_S = 15 * 60

# Turns in which a message may be answered: a short note ("looking it up now")
# and then the answer. The gate cannot tell that note-then-answer apart from an
# answer followed by a research post, so each message allows one extra post.
POSTS_PER_MESSAGE = 2


@dataclass(frozen=True)
class Refusal:
    """Why a post may not go out."""
    reason: str


@dataclass
class _Waiting:
    """A message in `chat_id` that has not been answered yet."""
    chat_id: str
    since: float
    posts_left: int = POSTS_PER_MESSAGE
    notified: bool = False


class ReplyGate:
    """Tracks which messages in which chats are still waiting for an answer."""

    def __init__(self, clock=time.monotonic, waiting_for_s=WAITING_FOR_S):
        self._clock = clock
        self._waiting_for_s = waiting_for_s
        self._turn = 0
        self._waiting = []  # oldest first
        self._answered = None  # (chat_id, turn) of the reply delivered last

    def next_turn(self):
        """The agent asked for its next message."""
        self._turn += 1

    def message_arrived(self, chat_id):
        """A message from `chat_id` was handed to the agent this turn."""
        self._waiting.append(_Waiting(str(chat_id), self._clock()))

    def check(self, chat_id):
        """None if a post to `chat_id` may go out now, else the Refusal."""
        # Before the agent's first turn: core's startup banner.
        if self._turn == 0:
            return None
        chat = str(chat_id)
        # Further pieces of a reply delivered this turn.
        if self._answered == (chat, self._turn):
            return None
        if not self._live_waiting(chat):
            return Refusal("no message in this chat is waiting for an answer")
        return None

    def delivered(self, chat_id):
        """A post to `chat_id` went out; it counts against the oldest message
        waiting there."""
        chat = str(chat_id)
        if self._answered == (chat, self._turn):
            return  # another piece of the same reply
        waiting = self._live_waiting(chat)
        if waiting:
            waiting[0].posts_left -= 1
            if waiting[0].posts_left == 0:
                self._waiting.remove(waiting[0])
        self._answered = (chat, self._turn)

    def claim_notice(self, chat_id):
        """Whether `chat_id` should hear that something went wrong: true once
        per waiting message that has had no post yet, and never otherwise. A
        user who already got a reply is not told about a later turn's failure."""
        unnotified = [w for w in self._live_waiting(str(chat_id))
                      if not w.notified and w.posts_left == POSTS_PER_MESSAGE]
        if not unnotified:
            return False
        unnotified[0].notified = True
        return True

    def _live_waiting(self, chat):
        """The messages waiting in `chat`, oldest first; ones too old to count
        are dropped."""
        now = self._clock()
        self._waiting = [w for w in self._waiting if now - w.since <= self._waiting_for_s]
        return [w for w in self._waiting if w.chat_id == chat]
