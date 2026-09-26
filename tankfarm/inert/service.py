"""Blanket gas service."""

from __future__ import annotations

from tankfarm.clock import LogicalClock
from tankfarm.domain import INERT_SEED_KPA, TANKS
from tankfarm.errors import UnknownTankError
from tankfarm.event import Event, EventBus
from tankfarm.event import topics
from tankfarm.inert.latch import VentLatch
from tankfarm.inert.model import InertState
from tankfarm.journal.writer import JournalWriter
from tankfarm.judgement.limits import BlanketLimits


class InertService:
    """Pressure confirmation, alarm handling and vent release."""

    def __init__(
        self,
        journal: JournalWriter,
        bus: EventBus,
        clock: LogicalClock,
        limits: BlanketLimits,
        vents: VentLatch,
    ) -> None:
        self._journal = journal
        self._bus = bus
        self._clock = clock
        self._limits = limits
        self._vents = vents
        self._states: dict[str, InertState] = {}
        self.seed()

    def seed(self) -> None:
        self._states = {}
        for tank_id in TANKS:
            self._states[tank_id] = InertState(
                tank_id=tank_id,
                pressure=INERT_SEED_KPA,
                alarm=False,
                confirmed=True,
                status=self._limits.classify(INERT_SEED_KPA),
            )

    def state(self, tank_id: str) -> InertState:
        state = self._states.get(tank_id)
        if state is None:
            raise UnknownTankError(tank_id)
        return state

    def states(self) -> tuple[InertState, ...]:
        return tuple(self._states.values())

    def refresh_all(self) -> None:
        """Reclassify every tank after the pressure window moved."""
        for state in self._states.values():
            self._refresh(state)

    def check(self, tank_id: str) -> bool:
        """The blanket is usable only while the pressure sits in its window."""
        return self.state(tank_id).confirmed

    def set_pressure(self, tank_id: str, pressure: float) -> InertState:
        state = self.state(tank_id)
        state.pressure = float(pressure)
        payload = {"tank_id": tank_id, "pressure": state.pressure}
        self._journal.append(topics.INERT_PRESSURE, payload)
        self._bus.publish(Event(topics.INERT_PRESSURE, payload, self._clock.now()))
        self._refresh(state)
        return state

    def alarm(self, tank_id: str) -> InertState:
        state = self.state(tank_id)
        state.manual_alarm = True
        self._refresh(state)
        if state.alarm:
            self._emit_alarm(tank_id, True)
        return state

    def clear_alarm(self, tank_id: str) -> InertState:
        state = self.state(tank_id)
        state.manual_alarm = False
        self._refresh(state)
        self._emit_alarm(tank_id, state.alarm)
        return state

    def apply_pressure(self, tank_id: str, pressure: float) -> None:
        state = self._states.get(tank_id)
        if state is not None:
            state.pressure = float(pressure)
            self._refresh(state)

    def apply_alarm(self, tank_id: str, alarm: bool) -> None:
        state = self._states.get(tank_id)
        if state is None:
            return
        state.manual_alarm = bool(alarm)
        self._refresh(state)

    def _emit_alarm(self, tank_id: str, alarm: bool) -> None:
        payload = {"tank_id": tank_id, "alarm": alarm}
        self._journal.append(topics.INERT_ALARM, payload)
        self._bus.publish(Event(topics.INERT_ALARM, payload, self._clock.now()))

    def _refresh(self, state: InertState) -> None:
        in_window = self._limits.confirmed(state.pressure)
        state.status = self._limits.classify(state.pressure)
        state.alarm = state.manual_alarm or not in_window
        state.confirmed = in_window and not state.manual_alarm
