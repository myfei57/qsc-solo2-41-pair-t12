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
            )

    def state(self, tank_id: str) -> InertState:
        state = self._states.get(tank_id)
        if state is None:
            raise UnknownTankError(tank_id)
        return state

    def states(self) -> tuple[InertState, ...]:
        return tuple(self._states.values())

    def check(self, tank_id: str) -> bool:
        return not self.state(tank_id).alarm

    def set_pressure(self, tank_id: str, pressure: float) -> InertState:
        state = self.state(tank_id)
        state.pressure = float(pressure)
        payload = {"tank_id": tank_id, "pressure": state.pressure}
        self._journal.append(topics.INERT_PRESSURE, payload)
        self._bus.publish(Event(topics.INERT_PRESSURE, payload, self._clock.now()))
        return state

    def alarm(self, tank_id: str) -> InertState:
        state = self.state(tank_id)
        state.alarm = True
        state.confirmed = False
        payload = {"tank_id": tank_id, "alarm": True}
        self._journal.append(topics.INERT_ALARM, payload)
        self._bus.publish(Event(topics.INERT_ALARM, payload, self._clock.now()))
        return state

    def clear_alarm(self, tank_id: str) -> InertState:
        state = self.state(tank_id)
        state.alarm = False
        payload = {"tank_id": tank_id, "alarm": False}
        self._journal.append(topics.INERT_ALARM, payload)
        self._bus.publish(Event(topics.INERT_ALARM, payload, self._clock.now()))
        return state

    def apply_pressure(self, tank_id: str, pressure: float) -> None:
        state = self._states.get(tank_id)
        if state is not None:
            state.pressure = float(pressure)

    def apply_alarm(self, tank_id: str, alarm: bool) -> None:
        state = self._states.get(tank_id)
        if state is None:
            return
        state.alarm = bool(alarm)
        if alarm:
            state.confirmed = False
        else:
            self._refresh(state, state.pressure)

    def _refresh(self, state: InertState, pressure: float) -> None:
        state.pressure = float(pressure)
        state.confirmed = (not state.alarm) and self._limits.confirmed(state.pressure)
