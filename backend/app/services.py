from __future__ import annotations

import asyncio
import time
from collections import Counter, defaultdict, deque
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from statistics import median

from .config import (
    AVAILABLE_PARAMETERS,
    COMMUNICATION_EXPECTED_MAX_SECONDS,
    COMMUNICATION_EXPECTED_MIN_SECONDS,
    COMMUNICATION_FAILURE_MULTIPLIER,
    COMMUNICATION_FAILURE_SECONDS,
    COMMUNICATION_INTERVAL_WINDOW_SAMPLES,
    COMMUNICATION_WARNING_MULTIPLIER,
    COMMUNICATION_WARNING_SECONDS,
    EXPECTED_SAMPLE_INTERVAL_SECONDS,
    EVENT_HISTORY_MAXLEN,
    FREEZE_CONFIRMATION_EVALUATIONS,
    FREEZE_MIN_DURATION_SECONDS,
    FREEZE_ML_STRONG_CONFIDENCE,
    NETWORK_DELAY_WARNING_SECONDS,
    NODES,
    PEER_FRESHNESS_SECONDS,
    PEER_FRESHNESS_MULTIPLIER,
    PEER_MIN_SENSOR_HEALTH,
    PHYSICAL_RANGES,
    RECOVERY_HEALTHY_SAMPLES,
    SENSOR_HISTORY_MAXLEN,
    TRUSTED_HISTORY_MAXLEN,
)
from .database import init_db, save_event, save_health, save_reading
from .detector import HeuristicDetector
from .ml_detector import ml_service



class Engine:
    def __init__(self):
        init_db()
        self.clients = []
        self.task = None
        self.monitor_task = None
        self._lifecycle_lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()
        self.context_service = None
        self.maintenance_service = None
        self._reset_runtime_state()

    def _reset_runtime_state(self):
        self._live_state = self._new_processing_state()
        self._bind_live_state()

    @staticmethod
    def _new_processing_state():
        return SimpleNamespace(
            detector=HeuristicDetector(),
            hist=defaultdict(lambda: deque(maxlen=SENSOR_HISTORY_MAXLEN)),
            events=deque(maxlen=EVENT_HISTORY_MAXLEN),
            trusted=deque(maxlen=TRUSTED_HISTORY_MAXLEN),
            health={
                node: {
                    "temperature": 100.0,
                    "pressure": 100.0,
                    "humidity": 100.0,
                    "communication": 100.0,
                    "node": 100.0,
                }
                for node in NODES
            },
            sensor_state={
                node: {parameter: "healthy" for parameter in AVAILABLE_PARAMETERS}
                for node in NODES
            },
            healthy_streak={
                node: {parameter: 0 for parameter in AVAILABLE_PARAMETERS}
                for node in NODES
            },
            communication_state={node: "awaiting_data" for node in NODES},
            communication_recovery_streak={node: 0 for node in NODES},
            active_anomalies={node: {} for node in NODES},
            active_episodes={node: set() for node in NODES},
            freeze_candidate_streak={
                node: {parameter: 0 for parameter in AVAILABLE_PARAMETERS}
                for node in NODES
            },
            last_event_group_signature={node: None for node in NODES},
            peer_failovers={node: {} for node in NODES},
            total=0,
            latencies=[],
            last_seen={},
            last_arrival={},
            arrival_intervals={
                node: deque(maxlen=COMMUNICATION_INTERVAL_WINDOW_SAMPLES)
                for node in NODES
            },
            last_sequence={},
            last_uptime_ms={},
            seen_nodes=set(),
        )

    def _bind_live_state(self):
        state = self._live_state
        self.detector = state.detector
        self.hist = state.hist
        self.events = state.events
        self.trusted = state.trusted
        self.health = state.health
        self.sensor_state = state.sensor_state
        self.healthy_streak = state.healthy_streak
        self.communication_state = state.communication_state
        self.communication_recovery_streak = state.communication_recovery_streak
        self.active_anomalies = state.active_anomalies
        self.active_episodes = state.active_episodes
        self.freeze_candidate_streak = state.freeze_candidate_streak
        self.last_event_group_signature = state.last_event_group_signature
        self.peer_failovers = state.peer_failovers
        self.latencies = state.latencies
        self.last_seen = state.last_seen
        self.last_arrival = state.last_arrival
        self.arrival_intervals = state.arrival_intervals
        self.last_sequence = state.last_sequence
        self.last_uptime_ms = state.last_uptime_ms
        self.seen_nodes = state.seen_nodes

    @property
    def total(self):
        return self._live_state.total

    def _reset_all_state_for_testing(self):
        """Clear live runtime state for test isolation."""
        self._live_state = self._new_processing_state()
        self._bind_live_state()


    def now(self):
        return datetime.now(timezone.utc).isoformat()

    async def publish(self, event_type, data):
        """Broadcast without allowing one stale browser to stall telemetry.

        A dead/slow WebSocket can otherwise keep ``send_json`` pending while
        the live processing lock is held.  On a small Render instance that can
        create an ever-growing MQTT processing backlog.  Bound every send and
        evict clients that cannot accept a frame promptly.
        """
        clients = tuple(self.clients)
        if not clients:
            return

        async def send_with_timeout(client):
            try:
                await asyncio.wait_for(
                    client.send_json({"type": event_type, "data": data}),
                    timeout=2.0,
                )
                return None
            except Exception as exc:
                return exc

        results = await asyncio.gather(
            *(send_with_timeout(client) for client in clients),
            return_exceptions=False,
        )
        for client, result in zip(clients, results):
            if isinstance(result, Exception) and client in self.clients:
                self.clients.remove(client)
                try:
                    await client.close()
                except Exception:
                    pass

    @staticmethod
    def _component_label(event):
        parameter = event.get("parameter")
        anomaly_type = event["anomaly_type"]
        return f"{parameter}_{anomaly_type}" if parameter else anomaly_type

    def _update_active_anomalies(self, node, events, state=None):
        state = state or self._live_state
        priority = {
            "deviation": 20,
            "node_mismatch": 30,
            "sensor_disagreement": 40,
            "sensor_degradation": 50,
            "freeze": 80,
            "drift": 85,
            "data_loss": 90,
            "spike": 95,
            "out_of_range": 100,
            "data_corruption": 100,
            "communication_failure": 100,
        }
        for event in events:
            if event.get("event_type") == "recovery":
                continue
            parameter = event.get("parameter")
            key = parameter if parameter in AVAILABLE_PARAMETERS else None
            if event.get("event_type") == "communication" or event["anomaly_type"] == "communication_failure":
                key = "communication"
            if key is None:
                continue
            current = state.active_anomalies[node].get(key)
            if current:
                prefix = f"{key}_"
                current_type = current[len(prefix):] if current.startswith(prefix) else current
                if priority.get(event["anomaly_type"], 10) <= priority.get(current_type, 10):
                    continue
            state.active_anomalies[node][key] = self._component_label(event)

    def _multi_fault_event(self, node, timestamp, state=None):
        state = state or self._live_state
        components = sorted(set(state.active_anomalies[node].values()))
        if len(components) < 2:
            state.last_event_group_signature[node] = None
            return None
        signature = tuple(components)
        if signature == state.last_event_group_signature[node]:
            return None
        state.last_event_group_signature[node] = signature
        return {
            "node_id": node,
            "timestamp": timestamp,
            "event_type": "event_group",
            "event_group": "multi_fault",
            "anomaly_type": "multi_fault",
            "parameter": None,
            "suspected_sensor": None,
            "confidence": max(70, min(98, 60 + 10 * len(components))),
            "severity": "critical" if len(components) >= 3 else "high",
            "message": "Multiple simultaneous fault components are active.",
            "observed_value": None,
            "expected_value": None,
            "corrected_value": None,
            "reasons": ["multiple independently detected anomaly components remain active"],
            "factor_contributions": {"active_component_count": len(components)},
            "recommended_action": "Inspect each component event; individual events remain available.",
            "detector_mode": state.detector.mode,
            "model_version": state.detector.version,
            "active_anomalies": components,
        }

    def health_status(self, node, state=None):
        state = state or self._live_state
        score = round(state.health[node]["node"])
        if score >= 85:
            return "healthy"
        if score >= 65:
            return "observe"
        if score >= 35:
            return "inspect_soon"
        return "critical_inspection"

    def _parse_timestamp(self, value):
        if not value:
            return None
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc)
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)

    @staticmethod
    def _robust_expected_interval(state, node):
        intervals = sorted(
            value for value in state.arrival_intervals[node]
            if value > 0
        )
        if len(intervals) >= 5:
            trim = max(1, len(intervals) // 10)
            trimmed = intervals[trim:-trim]
            if trimmed:
                intervals = trimmed
        estimate = median(intervals) if intervals else EXPECTED_SAMPLE_INTERVAL_SECONDS
        return min(
            COMMUNICATION_EXPECTED_MAX_SECONDS,
            max(COMMUNICATION_EXPECTED_MIN_SECONDS, estimate),
        )

    def communication_timing(self, node, state=None):
        state = state or self._live_state
        expected = self._robust_expected_interval(state, node)
        warning = max(COMMUNICATION_WARNING_SECONDS, expected * COMMUNICATION_WARNING_MULTIPLIER)
        failure = max(
            COMMUNICATION_FAILURE_SECONDS,
            expected * COMMUNICATION_FAILURE_MULTIPLIER,
            warning + expected,
        )
        return expected, warning, failure

    def peer_freshness_threshold(self, node, state=None):
        state = state or self._live_state
        expected, _, _ = self.communication_timing(node, state)
        return max(PEER_FRESHNESS_SECONDS, expected * PEER_FRESHNESS_MULTIPLIER)

    def _debounce_freeze_events(self, reading, events, assessment, state):
        """Allow only strongly supported ML temperature-freeze events.

        Humidity/pressure freeze are intentionally unavailable until dedicated
        models are trained. Heuristics can never create a freeze event.
        """
        filtered = [event for event in events if event.get("anomaly_type") != "freeze"]
        node = reading["node_id"]
        for parameter in AVAILABLE_PARAMETERS:
            if parameter != "temperature":
                state.freeze_candidate_streak[node][parameter] = 0

        freeze_event = next(
            (
                event for event in events
                if event.get("anomaly_type") == "freeze"
                and event.get("parameter") == "temperature"
                and event.get("ml_source") == "environmental_random_forest"
            ),
            None,
        )
        if freeze_event is None:
            state.freeze_candidate_streak[node]["temperature"] = 0
            return filtered

        confidence = float(assessment.get("confidence") or 0.0)
        duration = float((assessment.get("feature_summary") or {}).get("ds_same_value_duration_s") or 0.0)
        ml_supported = (
            assessment.get("prediction") == "temperature_freeze"
            and confidence >= FREEZE_ML_STRONG_CONFIDENCE
            and duration >= FREEZE_MIN_DURATION_SECONDS
        )
        if not ml_supported:
            state.freeze_candidate_streak[node]["temperature"] = 0
            return filtered

        state.freeze_candidate_streak[node]["temperature"] += 1
        if state.freeze_candidate_streak[node]["temperature"] < FREEZE_CONFIRMATION_EVALUATIONS:
            return filtered

        freeze_event["freeze_duration_seconds"] = round(duration, 3)
        freeze_event["freeze_confirmation_evaluations"] = state.freeze_candidate_streak[node]["temperature"]
        filtered.append(freeze_event)
        return filtered

    def _prepare_timing(self, reading, state):
        node = reading["node_id"]
        received_monotonic = reading.pop("_received_monotonic", time.monotonic())
        received_datetime = self._parse_timestamp(reading.pop("_received_at", None)) or datetime.now(timezone.utc)
        simulated_delay = reading.pop("_network_delay_seconds", None)
        sensor_datetime = self._parse_timestamp(reading.get("timestamp"))
        if simulated_delay is not None:
            sensor_datetime = received_datetime - timedelta(seconds=simulated_delay)
        sensor_datetime = sensor_datetime or received_datetime
        received_at = received_datetime.isoformat()
        sensor_timestamp = sensor_datetime.isoformat()
        delay_seconds = max(0.0, (received_datetime - sensor_datetime).total_seconds())

        inter_arrival = reading.pop("_inter_arrival_override", None)
        if inter_arrival is None and node in state.last_arrival:
            inter_arrival = max(0.0, received_monotonic - state.last_arrival[node])
        reading["timestamp"] = sensor_timestamp
        reading["sensor_timestamp"] = sensor_timestamp
        reading["received_at"] = received_at
        reading["network_delay_seconds"] = round(delay_seconds, 3)
        reading["inter_arrival_time_seconds"] = round(inter_arrival, 3) if inter_arrival is not None else None
        state.last_arrival[node] = received_monotonic
        return received_monotonic

    def _recent_peer(self, node, peer_override, state):
        if peer_override is not None:
            return peer_override
        peers = self.get_healthy_peers(node, state=state)
        if not peers:
            return None
        result = {"node_id": "+".join(item["node_id"] for item in peers)}
        for parameter, key in (("temperature", "temperature_c"), ("humidity", "humidity_pct"), ("pressure", "pressure_hpa")):
            values = [item["reading"].get(key) for item in peers if item["reading"].get(key) is not None]
            result[key] = sum(values) / len(values) if values else None
        return result

    def get_healthy_peers(self, node_id, parameter=None, state=None):
        """Return recent, valid peers eligible for detection and trusted-value failover."""
        state = state or self._live_state
        peers = []
        parameters = (parameter,) if parameter else AVAILABLE_PARAMETERS
        now = time.monotonic()
        for peer_id in NODES:
            if peer_id == node_id or not state.hist[peer_id] or peer_id not in state.last_seen:
                continue
            if now - state.last_seen[peer_id] > self.peer_freshness_threshold(peer_id, state):
                continue
            if state.communication_state[peer_id] != "healthy":
                continue
            reading = state.hist[peer_id][-1]
            eligible = True
            for item in parameters:
                key = {"temperature": "temperature_c", "humidity": "humidity_pct", "pressure": "pressure_hpa"}[item]
                value = reading.get(key)
                low, high = PHYSICAL_RANGES[item]
                if value is None or not low <= value <= high:
                    eligible = False
                    break
                if state.health[peer_id][item] < PEER_MIN_SENSOR_HEALTH or state.sensor_state[peer_id][item] != "healthy":
                    eligible = False
                    break
                if item in state.active_anomalies[peer_id]:
                    eligible = False
                    break
            if eligible:
                peers.append({"node_id": peer_id, "reading": reading})
        return peers

    async def _apply_peer_failover(self, reading, trusted, events, corrected_parameters, state, mode):
        node = reading["node_id"]
        event_parameters = {event.get("parameter") for event in events if event.get("parameter") in AVAILABLE_PARAMETERS}
        for parameter in AVAILABLE_PARAMETERS:
            active = parameter in event_parameters or state.sensor_state[node][parameter] in ("anomalous", "recovering")
            previous = state.peer_failovers[node].get(parameter)
            if not active:
                if previous:
                    event_type = "peer_failover_ended"
                    await self.publish(event_type, {**previous, "ended_at": reading["timestamp"], "status": "ended"})
                    state.peer_failovers[node].pop(parameter, None)
                continue
            peers = self.get_healthy_peers(node, parameter, state=state)
            if not peers:
                continue
            key = {"temperature": "temperature_c", "humidity": "humidity_pct", "pressure": "pressure_hpa"}[parameter]
            values = [float(item["reading"][key]) for item in peers]
            estimate = round(sum(values) / len(values), 3)
            provenance = "peer_station_mean" if len(peers) >= 2 else "single_peer_estimate"
            source_nodes = [item["node_id"] for item in peers]
            detail = {
                "node_id": node, "parameter": parameter, "raw_value": reading.get(key),
                "trusted_value": estimate, "provenance": provenance, "source_nodes": source_nodes,
                "excluded_node": node, "confidence": 90 if len(peers) >= 2 else 65,
                "reason": f"{node} {parameter} is anomalous or recovering; trusted value uses {len(peers)} healthy nearby peer station(s).",
                "started_at": previous["started_at"] if previous else reading["timestamp"], "status": "active",
            }
            trusted[key] = estimate
            trusted["quality"] = "estimated"
            trusted["provenance"] = provenance
            trusted["provenance_detail"] = "peer_station_consensus"
            trusted["source_nodes"] = source_nodes
            trusted["excluded_node"] = node
            trusted["peer_failover"] = detail
            corrected_parameters.append(parameter)
            state.peer_failovers[node][parameter] = detail
            await self.publish("peer_failover_updated" if previous else "peer_failover_started", detail)

    def _timing_events(self, reading, state):
        node = reading["node_id"]
        timestamp = reading["timestamp"]
        events = []
        delay = reading.get("network_delay_seconds") or 0
        if delay > NETWORK_DELAY_WARNING_SECONDS:
            events.append(
                state.detector.network_event(
                    node, timestamp, "network_delay", delay, EXPECTED_SAMPLE_INTERVAL_SECONDS,
                    f"packet arrived {delay:.2f} seconds after its sensor timestamp",
                )
            )

        interval = reading.get("inter_arrival_time_seconds")
        previous_intervals = state.arrival_intervals[node]
        expected_interval, warning_threshold, failure_threshold = self.communication_timing(node, state)
        reading["communication_timing"] = {
            "expected_interval_seconds": round(expected_interval, 3),
            "warning_threshold_seconds": round(warning_threshold, 3),
            "failure_threshold_seconds": round(failure_threshold, 3),
            "peer_freshness_seconds": round(self.peer_freshness_threshold(node, state), 3),
        }
        if (
            interval is not None
            and 0 < interval <= failure_threshold
            and state.communication_state[node] not in ("delayed", "communication_failure")
        ):
            previous_intervals.append(interval)

        device = reading.get("device") or {}
        sequence = device.get("sequence")
        uptime_ms = device.get("uptime_ms")
        previous_sequence = state.last_sequence.get(node)
        previous_uptime_ms = state.last_uptime_ms.get(node)

        # ESP32 sequence counters restart at zero when the board reboots.
        # Treat a large uptime rollback plus a small restarted sequence as a
        # device reboot, not as an out-of-order packet. A small uptime rollback
        # can still occur when genuinely older packets arrive late, so it does
        # not reset the sequence baseline.
        reboot_detected = (
            sequence is not None
            and uptime_ms is not None
            and previous_sequence is not None
            and previous_uptime_ms is not None
            and sequence <= 20
            and uptime_ms + 5000 < previous_uptime_ms
        )

        if reboot_detected:
            state.last_sequence[node] = sequence
            state.last_uptime_ms[node] = uptime_ms
            reading["device_restart_detected"] = True
        else:
            if sequence is not None and previous_sequence is not None:
                if sequence <= previous_sequence:
                    events.append(
                        state.detector.network_event(
                            node, timestamp, "out_of_order_packet", sequence, previous_sequence + 1,
                            f"sequence {sequence} arrived after sequence {previous_sequence}",
                        )
                    )
                elif sequence > previous_sequence + 1:
                    events.append(
                        state.detector.network_event(
                            node, timestamp, "packet_gap", sequence, previous_sequence + 1,
                            f"missing sequence values between {previous_sequence} and {sequence}",
                        )
                    )
            if sequence is not None and (previous_sequence is None or sequence > previous_sequence):
                state.last_sequence[node] = sequence
            if uptime_ms is not None and (previous_uptime_ms is None or uptime_ms >= previous_uptime_ms):
                state.last_uptime_ms[node] = uptime_ms

        return events

    async def process(self, reading, peer_override=None):
        async with self._state_lock:
            if reading.get("source") == "simulator" or bool((reading.get("simulation") or {}).get("enabled")):
                raise ValueError("Runtime simulation is disabled; production ingest accepts real AWS telemetry only")
            return await self._process_reading(reading, peer_override, state=self._live_state, mode="live")

    async def _process_reading(self, reading, peer_override=None, *, state=None, mode="live"):
        state = state or self._live_state
        persist = mode == "live"
        started = time.perf_counter()
        reading = deepcopy(reading)
        node = reading["node_id"]
        received_monotonic = self._prepare_timing(reading, state)
        reading["quality"] = "raw"
        peer = self._recent_peer(node, peer_override, state)
        history = list(state.hist[node])
        events = state.detector.detect(reading, history, peer)

        # Hybrid RF v6 is additive: it can confirm/emit temperature spike, drift
        # or freeze events, while heuristic-v2 remains the safety net for peer,
        # multivariate, humidity, pressure, degradation and communication logic.
        ml_assessment = ml_service.assess(reading, history)
        reading["ml_assessment"] = ml_assessment
        events = ml_service.fuse_events(reading, events, ml_assessment)
        events = self._debounce_freeze_events(reading, events, ml_assessment, state)
        events.extend(self._timing_events(reading, state))

        previous_communication = state.communication_state[node]
        communication_recovery = None
        if previous_communication in ("delayed", "communication_failure", "recovering"):
            state.communication_state[node] = "recovering"
            _, warning_threshold, _ = self.communication_timing(node, state)
            interval = reading.get("inter_arrival_time_seconds")
            if previous_communication in ("delayed", "communication_failure"):
                state.communication_recovery_streak[node] = 1
            elif interval is None or interval <= warning_threshold:
                state.communication_recovery_streak[node] += 1
            else:
                state.communication_recovery_streak[node] = 0
            if state.communication_recovery_streak[node] >= RECOVERY_HEALTHY_SAMPLES:
                state.communication_state[node] = "healthy"
                state.communication_recovery_streak[node] = 0
                communication_recovery = state.detector.recovery_event(
                    node, reading["timestamp"], "communication", RECOVERY_HEALTHY_SAMPLES
                )
        else:
            state.communication_state[node] = "healthy"
            state.communication_recovery_streak[node] = 0
        reading["communication_state"] = state.communication_state[node]

        trusted = deepcopy(reading)
        trusted["quality"] = "validated"
        trusted["provenance"] = "raw_validated"
        corrected_parameters = []
        await self._apply_peer_failover(reading, trusted, events, corrected_parameters, state, mode)

        disagreement = next(
            (
                event for event in events
                if event["anomaly_type"] in ("sensor_disagreement", "sensor_degradation")
                and event.get("suspected_sensor") == "ds18b20"
                and event.get("corrected_value") is not None
            ),
            None,
        )
        if disagreement and "temperature" not in corrected_parameters:
            trusted["temperature_c"] = disagreement["corrected_value"]
            trusted["primary_temperature_c"] = disagreement["corrected_value"]
            trusted["quality"] = "estimated"
            trusted["provenance"] = "rolling_median_or_reference_estimate"
            trusted["provenance_detail"] = "cross_sensor_consensus"
            corrected_parameters.append("temperature")
        elif "temperature" not in corrected_parameters and reading.get("primary_temperature_c") is None and reading.get("temperature_consensus_c") is not None:
            trusted["temperature_c"] = reading["temperature_consensus_c"]
            trusted["quality"] = "estimated"
            trusted["provenance"] = "cross_sensor_consensus"

        for event in sorted(events, key=lambda item: item["confidence"], reverse=True):
            parameter = event.get("parameter")
            if parameter in corrected_parameters or event.get("corrected_value") is None:
                continue
            key = {"temperature": "temperature_c", "humidity": "humidity_pct", "pressure": "pressure_hpa"}.get(parameter)
            if key:
                trusted[key] = event["corrected_value"]
                corrected_parameters.append(parameter)
                trusted["quality"] = "estimated"
                trusted["provenance"] = (
                    "peer_reference_estimate" if event["anomaly_type"] == "node_mismatch" else "rolling_median_estimate"
                )
        if corrected_parameters:
            trusted["corrected_parameters"] = corrected_parameters

        state.hist[node].append(reading)
        state.trusted.append({"raw": deepcopy(reading), "trusted": deepcopy(trusted)})
        state.total += 1
        state.last_seen[node] = received_monotonic
        state.seen_nodes.add(node)
        if persist:
            save_reading(reading, trusted)
        if persist and self.maintenance_service is not None:
            await self.maintenance_service.on_reading(reading)

        impacting_events = [
            event
            for event in events
            if event.get("event_type", "anomaly") not in ("recovery", "event_group")
        ]
        anomalous_parameters = {event["parameter"] for event in impacting_events if event.get("parameter") in AVAILABLE_PARAMETERS}
        self._update_active_anomalies(node, impacting_events, state)
        for event in events:
            event.setdefault("timestamp", reading["timestamp"])
            await self._record_event(
                event,
                affect_health=event.get("event_type", "anomaly") != "event_group",
                state=state,
                mode=mode,
            )

        recovery_events = []
        for parameter in AVAILABLE_PARAMETERS:
            if parameter in anomalous_parameters:
                state.sensor_state[node][parameter] = "anomalous"
                state.healthy_streak[node][parameter] = 0
                continue
            if state.sensor_state[node][parameter] in ("anomalous", "recovering"):
                state.sensor_state[node][parameter] = "recovering"
                state.healthy_streak[node][parameter] += 1
                if state.healthy_streak[node][parameter] >= RECOVERY_HEALTHY_SAMPLES:
                    state.sensor_state[node][parameter] = "healthy"
                    state.active_anomalies[node].pop(parameter, None)
                    self._close_anomaly_episodes(state, node, parameter)
                    recovery_events.append(
                        state.detector.recovery_event(
                            node, reading["timestamp"], parameter, RECOVERY_HEALTHY_SAMPLES
                        )
                    )
            state.health[node][parameter] = min(100, state.health[node][parameter] + 0.5)

        network_fault = any(event.get("event_type") == "communication" for event in impacting_events)
        if not network_fault:
            state.health[node]["communication"] = min(100, state.health[node]["communication"] + 0.5)
        if not impacting_events:
            state.health[node]["node"] = min(100, state.health[node]["node"] + 0.5)

        if communication_recovery:
            state.active_anomalies[node].pop("communication", None)
            self._close_anomaly_episodes(state, node, "communication")
            recovery_events.append(communication_recovery)

        group_event = self._multi_fault_event(node, reading["timestamp"], state)
        if group_event:
            await self._record_event(group_event, affect_health=False, state=state, mode=mode)
        for event in recovery_events:
            await self._record_event(event, affect_health=False, state=state, mode=mode)

        health_payload = self.health_payload(node, reading["timestamp"], state=state)
        if persist:
            save_health(health_payload)
        state.latencies.append((time.perf_counter() - started) * 1000)
        await self.publish("sensor_reading", reading)
        await self.publish("trusted_reading", {"raw": reading, "trusted": trusted})
        await self.publish("sensor_health", health_payload)
        if persist and self.context_service is not None:
            await self.context_service.on_sensor_update(node)
        if previous_communication != state.communication_state[node]:
            await self.publish(
                "communication_status",
                self.communication_payload(node, state=state, mode="live"),
            )
        return {
            "raw": reading,
            "trusted": trusted,
            "events": events + ([group_event] if group_event else []) + recovery_events,
            "health": health_payload,
        }

    @staticmethod
    def _episode_key(event):
        parameter = event.get("parameter")
        if event.get("event_type") == "communication" or event["anomaly_type"] == "communication_failure":
            component = "communication"
        elif parameter in AVAILABLE_PARAMETERS:
            component = parameter
        elif event["anomaly_type"] == "multivariate_inconsistency":
            component = "temperature+humidity"
        else:
            component = "__node__"
        return component, event["anomaly_type"]

    @staticmethod
    def _close_anomaly_episodes(state, node, component):
        state.active_episodes[node] = {
            episode
            for episode in state.active_episodes[node]
            if episode[0] != component
        }
        if component in AVAILABLE_PARAMETERS and all(
            state.sensor_state[node][parameter] == "healthy"
            for parameter in AVAILABLE_PARAMETERS
        ):
            state.active_episodes[node] = {
                episode
                for episode in state.active_episodes[node]
                if episode[0] == "communication"
            }

    async def _record_event(self, event, affect_health=True, *, state=None, mode="live"):
        state = state or self._live_state
        state.events.appendleft(event)
        save_event(event)
        node = event["node_id"]
        episode_key = self._episode_key(event)
        new_episode = affect_health and episode_key not in state.active_episodes[node]
        if new_episode:
            state.active_episodes[node].add(episode_key)
            parameter = event.get("parameter")
            if parameter in AVAILABLE_PARAMETERS:
                state.health[node][parameter] = max(0, state.health[node][parameter] - event["confidence"] / 12)
            elif event["anomaly_type"] == "multivariate_inconsistency":
                for affected in ("temperature", "humidity"):
                    state.health[node][affected] = max(0, state.health[node][affected] - event["confidence"] / 16)
            if event.get("event_type") == "communication" or event["anomaly_type"] == "communication_failure":
                state.health[node]["communication"] = max(0, state.health[node]["communication"] - event["confidence"] / 12)
            state.health[node]["node"] = max(0, state.health[node]["node"] - event["confidence"] / 18)

        event_type = event.get("event_type", "anomaly")
        if event_type == "recovery":
            await self.publish("recovery", event)
        else:
            await self.publish("anomaly", event)
            if event["anomaly_type"] in ("sensor_disagreement", "sensor_degradation"):
                await self.publish("sensor_consistency", event)
            if event_type == "communication":
                await self.publish("communication_status", event)

    def health_payload(self, node, timestamp=None, *, state=None):
        state = state or self._live_state
        return {
            "node_id": node,
            "timestamp": timestamp or self.now(),
            **{key: round(value, 1) for key, value in state.health[node].items()},
            "communication_quality": round(state.health[node]["communication"], 1),
            "communication_state": state.communication_state[node],
            "sensor_states": dict(state.sensor_state[node]),
            "status": self.health_status(node, state),
        }

    async def _emit_data_loss(self, node, fault_type="data_loss", parameter=None, *, state=None, mode="live"):
        state = state or self._live_state
        if fault_type == "communication_failure" and state.communication_state[node] == "communication_failure":
            return
        event = state.detector.detect_missing(node, self.now(), fault_type)
        event["parameter"] = parameter if fault_type == "data_loss" else None
        if fault_type == "communication_failure":
            event["event_type"] = "communication"
            state.communication_state[node] = "communication_failure"
            state.communication_recovery_streak[node] = 0
            state.active_anomalies[node]["communication"] = "communication_failure"
        else:
            state.last_seen[node] = time.monotonic()
            state.seen_nodes.add(node)
            affected = (parameter,) if parameter in AVAILABLE_PARAMETERS else AVAILABLE_PARAMETERS
            for sensor_parameter in affected:
                state.sensor_state[node][sensor_parameter] = "anomalous"
                state.healthy_streak[node][sensor_parameter] = 0
                state.active_anomalies[node][sensor_parameter] = f"{sensor_parameter}_data_loss"
        await self._record_event(event, affect_health=True, state=state, mode=mode)
        health_payload = self.health_payload(node, event["timestamp"], state=state)
        save_health(health_payload)
        await self.publish("sensor_health", health_payload)
        await self.publish(
            "communication_status",
            self.communication_payload(node, state=state, mode="live"),
        )

    async def check_communication(self, now_monotonic=None):
        async with self._state_lock:
            return await self._check_communication_unlocked(now_monotonic)

    async def _check_communication_state_unlocked(self, now_monotonic=None, *, state, mode):
        current = now_monotonic if now_monotonic is not None else time.monotonic()
        transitions = []
        for node in state.seen_nodes:
            elapsed = current - state.last_seen[node]
            previous = state.communication_state[node]
            _, warning_threshold, failure_threshold = self.communication_timing(node, state)
            if elapsed >= failure_threshold and previous != "communication_failure":
                state.communication_state[node] = "communication_failure"
                state.communication_recovery_streak[node] = 0
                event = state.detector.detect_missing(
                    node,
                    self.now(),
                    "communication_failure",
                    f"no packet received for {elapsed:.1f} seconds",
                )
                event["event_type"] = "communication"
                state.active_anomalies[node]["communication"] = "communication_failure"
                await self._record_event(event, affect_health=True, state=state, mode=mode)
                transitions.append(event)
            elif elapsed >= warning_threshold and previous in ("healthy", "recovering"):
                state.communication_state[node] = "delayed"
                state.communication_recovery_streak[node] = 0
                payload = self.communication_payload(node, elapsed, state=state, mode=mode)
                await self.publish("communication_status", payload)
                transitions.append(payload)
        return transitions

    async def _check_communication_unlocked(self, now_monotonic=None, *, state=None, mode="live"):
        return await self._check_communication_state_unlocked(
            now_monotonic,
            state=state or self._live_state,
            mode=mode,
        )

    def communication_payload(self, node, age_seconds=None, *, state=None, mode="live"):
        state = state or self._live_state
        if age_seconds is None and node in state.last_seen:
            age_seconds = max(0, time.monotonic() - state.last_seen[node])
        expected, warning, failure = self.communication_timing(node, state)
        payload = {
            "node_id": node,
            "state": state.communication_state[node],
            "seconds_since_last_seen": round(age_seconds, 2) if age_seconds is not None else None,
            "communication_quality": round(state.health[node]["communication"], 1),
            "expected_interval_seconds": round(expected, 2),
            "warning_threshold_seconds": round(warning, 2),
            "failure_threshold_seconds": round(failure, 2),
            "peer_freshness_seconds": round(self.peer_freshness_threshold(node, state), 2),
        }
        return payload

    async def shutdown(self):
        task = self.monitor_task
        self.monitor_task = None
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def start_monitor(self):
        if not self.monitor_task or self.monitor_task.done():
            self.monitor_task = asyncio.create_task(
                self._communication_monitor_loop(), name="skyguard-communication-monitor"
            )

    async def _communication_monitor_loop(self):
        try:
            while True:
                await asyncio.sleep(1)
                await self.check_communication()
                if self.context_service is not None:
                    await self.context_service.refresh_expiry()
        except asyncio.CancelledError:
            raise

    def metrics(self):
        return {
            "total_readings": self.total,
            "anomaly_count": len(self.events),
            "anomalies_by_type": dict(Counter(event["anomaly_type"] for event in self.events)),
            "active_nodes": sum(bool(self.hist[node]) for node in NODES),
            "average_processing_latency_ms": round(sum(self.latencies) / len(self.latencies), 3) if self.latencies else 0,
            "communication_states": dict(self.communication_state),
            "peer_failovers_active": sum(len(items) for items in self.peer_failovers.values()),
        }

    def dashboard_summary(self):
        node_summaries = []
        for node in NODES:
            latest = deepcopy(self.hist[node][-1]) if self.hist[node] else None
            node_summaries.append(
                {
                    "node_id": node,
                    "status": self.health_status(node),
                    "health_score": round(self.health[node]["node"], 1),
                    "communication_quality": round(self.health[node]["communication"], 1),
                    "communication_state": self.communication_state[node],
                    "latest": latest,
                    "active_anomalies": sorted(set(self.active_anomalies[node].values())),
                    "peer_failovers": list(self.peer_failovers[node].values()),
                }
            )
        statuses = {item["status"] for item in node_summaries}
        system_status = "critical" if "critical_inspection" in statuses else "warning" if statuses != {"healthy"} else "healthy"
        return {
            "system": {
                "status": system_status,
                "detector_mode": ml_service.combined_mode,
                "ml_model": ml_service.status(),
            },
            "nodes": node_summaries,
            "recent_events": list(self.events)[:20],
            "metrics": self.metrics(),
        }


engine = Engine()
