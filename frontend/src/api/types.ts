export type NodeId = "AWS_001" | "AWS_002" | "AWS_003";
export type Parameter = "temperature" | "humidity" | "pressure";
export type ConnectionStatus = "connecting" | "connected" | "reconnecting" | "disconnected";
export type DataMode = "live" | "demo";

export type FieldReportCategory =
  | "clouds_approaching" | "light_rain" | "heavy_rain" | "thunderstorm"
  | "strong_wind" | "fog" | "low_visibility" | "hail" | "dust_storm"
  | "rapid_weather_change" | "flooding" | "other_weather"
  | "sensor_damage" | "sensor_obstruction" | "sensor_contamination"
  | "power_issue" | "communication_issue" | "suspected_sensor_fault"
  | "station_access_issue" | "other_station_issue" | "custom_observation";
export type FieldReportSeverity = "information" | "low" | "moderate" | "high" | "critical";
export type ReporterConfidence = "low" | "medium" | "high";
export type FieldReportStatus = "active" | "resolved" | "expired";

export interface NearbyStation {
  node_id: NodeId;
  distance_km: number | null;
  location_label: string;
}

export interface MonitoringContextItem {
  report_id: string;
  type: FieldReportCategory;
  severity: FieldReportSeverity;
  distance_km: number | null;
  verification_state: string;
}

export interface MonitoringContext {
  node_id: NodeId;
  active_context: MonitoringContextItem[];
  monitoring_mode: "normal" | "heightened" | "priority" | "inspection_required";
  recommended_sample_interval_seconds: number;
  baseline_sample_interval_seconds: number;
}

export interface FieldReport {
  id: string;
  created_at: string;
  updated_at: string;
  reporter_type: string;
  reporter_name?: string | null;
  station_id?: NodeId | null;
  cluster_id?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  location_label?: string | null;
  category: FieldReportCategory;
  observation: string;
  severity: FieldReportSeverity;
  reporter_confidence: ReporterConfidence;
  direction?: string | null;
  radius_km: number;
  status: FieldReportStatus;
  expires_at?: string | null;
  source: string;
  verification_state: string;
  corroboration_confidence: number;
  verified_by_nodes: NodeId[];
  evidence: string[];
  contradicting_evidence: string[];
  message: string;
  notes?: string | null;
  resolved_at?: string | null;
  nearby_stations: NearbyStation[];
}

export interface FieldReportCreatePayload {
  reporter_type: string;
  reporter_name?: string;
  station_id?: NodeId | null;
  cluster_id?: string | null;
  latitude?: number;
  longitude?: number;
  location_label?: string;
  category: FieldReportCategory;
  observation: string;
  severity: FieldReportSeverity;
  reporter_confidence: ReporterConfidence;
  direction?: string | null;
  radius_km: number;
  expires_in_minutes?: number | null;
  until_resolved?: boolean;
  source: string;
  notes?: string;
}

export interface RawSensors {
  ds18b20_temperature_c?: number | null;
  dht22_temperature_c?: number | null;
  dht22_humidity_pct?: number | null;
  bmp180_temperature_c?: number | null;
  bmp180_pressure_hpa?: number | null;
  bmp280_temperature_c?: number | null;
  bmp280_pressure_hpa?: number | null;
  barometric_temperature_c?: number | null;
  pressure_hpa?: number | null;
  legacy_temperature_c?: number | null;
  legacy_humidity_pct?: number | null;
  legacy_pressure_hpa?: number | null;
}

export interface DeviceMetadata {
  wifi_rssi?: number | null;
  uptime_ms?: number | null;
  sequence?: number | null;
}

export interface MLAssessment {
  enabled: boolean;
  loaded: boolean;
  detector_mode: string;
  model_version: string;
  prediction?: string | null;
  confidence?: number | null;
  probabilities?: Record<string, number>;
  ready?: boolean;
  source?: string;
  reason?: string;
  suppressed_prediction?: string;
  suppressed_reason?: string;
  feature_summary?: Record<string, number>;
  samples?: number;
  window_30s_points?: number;
  window_60s_points?: number;
  window_span_seconds?: number;
}

export interface MLStatus {
  enabled: boolean;
  loaded: boolean;
  model_version?: string | null;
  detector_mode: string;
  model_path?: string;
  feature_count: number;
  confidence_threshold: number;
  min_samples: number;
  predictions: number;
  predictions_by_label: Record<string, number>;
  last_prediction_at?: string | null;
  error?: string | null;
  fallback?: string | null;
}

export interface SensorReading {
  node_id: NodeId;
  timestamp: string;
  sensor_timestamp?: string;
  received_at?: string;
  source?: string;
  temperature_c?: number | null;
  humidity_pct?: number | null;
  pressure_hpa?: number | null;
  primary_temperature_c?: number | null;
  reference_temperature_c?: number | null;
  temperature_consensus_c?: number | null;
  raw_sensors?: RawSensors;
  sensors?: RawSensors;
  device?: DeviceMetadata;
  missing_sensors?: string[];
  normalization_mode?: "legacy" | "multi_sensor" | string;
  network_delay_seconds?: number | null;
  inter_arrival_time_seconds?: number | null;
  quality?: string;
  communication_state?: string;
  provenance?: string;
  provenance_detail?: string;
  corrected_parameters?: string[];
  source_nodes?: NodeId[];
  excluded_node?: NodeId;
  peer_failover?: PeerFailover;
  ml_assessment?: MLAssessment;
}

export interface NodeListItem {
  node_id: NodeId;
  active: boolean;
  communication_state: string;
}

export interface NodeSummary {
  node_id: NodeId;
  status: string;
  health_score: number;
  communication_quality: number;
  communication_state: string;
  latest: SensorReading | null;
  active_anomalies: string[];
  monitoring_context?: MonitoringContext;
  peer_failovers?: PeerFailover[];
}

export interface PeerFailover {
  parameter: Parameter;
  provenance: "peer_station_mean" | "single_peer_estimate";
  source_nodes: NodeId[];
  excluded_node: NodeId;
  trusted_value: number;
  confidence: number;
  reason: string;
  state?: string;
}

export interface AnomalyEvent {
  id?: number | string | null;
  node_id: NodeId | string;
  timestamp: string;
  anomaly_type: string;
  event_type: string;
  parameter?: string | null;
  affected_sensor?: string | null;
  suspected_sensor?: string | null;
  confidence: number;
  severity: string;
  message: string;
  observed_value?: number | null;
  expected_value?: number | null;
  corrected_value?: number | null;
  reasons: string[];
  factor_contributions: Record<string, number>;
  recommended_action: string;
  detector_mode: string;
  model_version: string;
  healthy_samples?: number;
  freeze_duration_samples?: number;
  event_group?: string | null;
  active_anomalies?: string[] | null;
  ml_prediction?: string | null;
  ml_confidence?: number | null;
  ml_probabilities?: Record<string, number>;
  ml_source?: string | null;
  heuristic_detector_mode?: string | null;
  heuristic_model_version?: string | null;
  mode?: "live" | string;
}

export interface SensorHealth {
  node_id: NodeId;
  timestamp: string;
  temperature: number;
  pressure: number;
  humidity: number;
  communication: number;
  node: number;
  communication_quality: number;
  communication_state: string;
  sensor_states: Record<Parameter, string>;
  status: string;
}

export interface TrustedReading {
  raw: SensorReading;
  trusted: SensorReading;
}

export interface NodeRecordsResponse {
  node_id: NodeId;
  records: TrustedReading[];
}

export interface MqttStatus {
  enabled: boolean;
  connected: boolean;
  broker: string;
  topic: string;
  messages_received: number;
  messages_rejected: number;
  last_connected_at?: string | null;
  last_message_at?: string | null;
}

export interface HealthResponse {
  status: string;
  service: string;
  detector_mode: string;
  ml?: MLStatus;
  mqtt: MqttStatus;
}

export interface Metrics {
  total_readings: number;
  anomaly_count: number;
  anomalies_by_type: Record<string, number>;
  active_nodes: number;
  average_processing_latency_ms: number;
  communication_states: Record<NodeId, string>;
  peer_failovers_active?: number;
}

export interface DashboardSummary {
  system: {
    status: string;
    detector_mode: string;
    stations_online?: string;
    vision_systems_online?: number;
    vision_systems_total?: number;
    peer_failovers_active?: number;
    maintenance_warnings?: number;
    community_reports?: number;
    ml_model?: MLStatus;
  };
  nodes: NodeSummary[];
  recent_events: AnomalyEvent[];
  metrics: Metrics;
}

export interface IngestPayload {
  node_id: NodeId;
  timestamp?: string;
  temperature_c?: number;
  pressure_hpa?: number;
  humidity_pct?: number;
  sensors?: RawSensors;
  device?: DeviceMetadata;
  source?: string;
}

export type WebSocketEventType =
  | "system_status"
  | "sensor_reading"
  | "trusted_reading"
  | "anomaly"
  | "sensor_health"
  | "communication_status"
  | "sensor_consistency"
  | "recovery"
  | "field_report_created"
  | "field_report_updated"
  | "field_report_resolved"
  | "field_report_expired"
  | "field_report_corroboration"
  | "node_monitoring_context"
  | "public_report_created"
  | "public_report_updated"
  | "public_report_verified"
  | "public_report_resolved"
  | "public_report_corroboration"
  | "vision_event"
  | "vision_status"
  | "peer_failover_started"
  | "peer_failover_updated"
  | "peer_failover_ended"
  | "maintenance_status"
  | "sensor_exposure"
  | "multi_source_context";

export interface VisionDetection {
  type: string;
  confidence: number;
}

export interface VisionEvent {
  id?: number;
  node_id: NodeId;
  timestamp: string;
  source: "camera" | "manual_camera" | "simulated_camera" | string;
  vision_mode: "manual" | "simulated" | string;
  type: string;
  confidence: number;
  severity: string;
  message: string;
  recommended_action: string;
  event_group: string;
}

export interface VisionStatus {
  node_id: NodeId;
  camera_status: "online" | "offline";
  latest_analysis: VisionEvent | null;
  vision_mode: string;
  last_observation_at: string | null;
}

export interface SensorSpec {
  manufacturer: string;
  model: string;
  measurement_temperature_min_c: number;
  measurement_temperature_max_c: number;
  operating_humidity_limits?: [number, number] | null;
  pressure_range_hpa?: [number, number] | null;
  accuracy_specification: string;
  expected_service_life: null;
  datasheet_reference: string;
  notes: string;
}

export interface SensorInventoryItem {
  sensor_id: string;
  node_id: NodeId;
  model: "DS18B20" | "DHT22" | "BMP280";
  installed_at: string;
  last_inspection_at?: string | null;
  last_calibration_at?: string | null;
}

export interface MaintenanceStatus {
  sensor_id: string;
  node_id: NodeId;
  model: string;
  health_score: number;
  maintenance_priority: string;
  reasons: string[];
  recommended_action: string;
  scoring_mode: "heuristic_datasheet_exposure";
  expected_service_life: null;
}

export interface AdminSession {
  authenticated: boolean;
  role: "admin";
  name: string;
  token: string;
  expires_at: string;
}

export interface PublicReportPayload {
  category: FieldReportCategory;
  observation: string;
  station_scope: NodeId[];
  direction?: string | null;
  severity: FieldReportSeverity;
  reporter_confidence: ReporterConfidence;
  location_label?: string;
  latitude?: number;
  longitude?: number;
  radius_km: number;
  expires_in_minutes?: number;
}

export interface WebSocketEnvelope<T = unknown> {
  type: WebSocketEventType;
  data: T;
}
