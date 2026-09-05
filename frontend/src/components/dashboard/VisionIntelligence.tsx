import { useEffect, useRef, useState } from "react"
import {
  Camera,
  CameraOff,
  RefreshCw,
  ShieldCheck,
  TriangleAlert,
} from "lucide-react"
import { api } from "../../api/endpoints"
import type { NodeId } from "../../api/types"
import { useSkyGuard } from "../../hooks/useSkyGuard"
import {
  displayNodeId,
  formatRelativeTime,
  titleCase,
} from "../../utils/format"
import { EmptyState, PanelHeading, StatusPill } from "./StatusUi"

const nodes: NodeId[] = ["AWS_001", "AWS_002", "AWS_003"]
const GRID_W = 48
const GRID_H = 32
const ANALYZE_EVERY_MS = 700
const CALIBRATION_FRAMES = 5
const REPORT_COOLDOWN_MS = 15_000

type VisionState =
  | "idle"
  | "calibrating"
  | "clear"
  | "obstacle"
  | "blocked"
  | "error"

interface FrameStats {
  pixels: Float32Array
  mean: number
  stdev: number
  edge: number
}

function clamp01(value: number) {
  return Math.max(0, Math.min(1, value))
}

function frameStats(pixels: Float32Array): FrameStats {
  let sum = 0
  for (const value of pixels) sum += value
  const mean = sum / Math.max(1, pixels.length)

  let variance = 0
  let edgeTotal = 0
  let edgeCount = 0
  for (let y = 0; y < GRID_H; y += 1) {
    for (let x = 0; x < GRID_W; x += 1) {
      const index = y * GRID_W + x
      const value = pixels[index]
      variance += (value - mean) ** 2
      if (x > 0) {
        edgeTotal += Math.abs(value - pixels[index - 1])
        edgeCount += 1
      }
      if (y > 0) {
        edgeTotal += Math.abs(value - pixels[index - GRID_W])
        edgeCount += 1
      }
    }
  }

  return {
    pixels,
    mean,
    stdev: Math.sqrt(variance / Math.max(1, pixels.length)),
    edge: edgeTotal / Math.max(1, edgeCount),
  }
}

function averageFrames(frames: Float32Array[]) {
  const average = new Float32Array(GRID_W * GRID_H)
  if (!frames.length) return average
  for (const frame of frames) {
    for (let index = 0; index < average.length; index += 1) {
      average[index] += frame[index]
    }
  }
  for (let index = 0; index < average.length; index += 1) {
    average[index] /= frames.length
  }
  return average
}

export default function VisionIntelligence({
  onNotify,
}: {
  onNotify: (message: string, tone?: "success" | "error") => void
}) {
  const {
    visionStatuses: statuses,
    visionEvents: events,
    refresh,
  } = useSkyGuard()
  const [selectedNode, setSelectedNode] = useState<NodeId>("AWS_001")
  const [cameraState, setCameraState] = useState<VisionState>("idle")
  const [obstructionScore, setObstructionScore] = useState(0)
  const [visibilityScore, setVisibilityScore] = useState(0)
  const [cameraMessage, setCameraMessage] = useState(
    "Start the camera, then keep the normal scene clear during calibration.",
  )

  const videoRef = useRef<HTMLVideoElement | null>(null)
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const baselineRef = useRef<FrameStats | null>(null)
  const calibrationRef = useRef<Float32Array[]>([])
  const obstacleStreakRef = useRef(0)
  const blockedStreakRef = useRef(0)
  const clearStreakRef = useRef(0)
  const lastReportedRef = useRef<{ type: string; at: number } | null>(null)

  function stopCamera() {
    if (streamRef.current) {
      for (const track of streamRef.current.getTracks()) track.stop()
    }
    streamRef.current = null
    if (videoRef.current) videoRef.current.srcObject = null
    baselineRef.current = null
    calibrationRef.current = []
    obstacleStreakRef.current = 0
    blockedStreakRef.current = 0
    clearStreakRef.current = 0
    setCameraState("idle")
    setObstructionScore(0)
    setVisibilityScore(0)
    setCameraMessage("Camera stopped.")
  }

  function beginCalibration() {
    baselineRef.current = null
    calibrationRef.current = []
    obstacleStreakRef.current = 0
    blockedStreakRef.current = 0
    clearStreakRef.current = 0
    setCameraState("calibrating")
    setObstructionScore(0)
    setCameraMessage("Calibrating normal scene for about 3 seconds…")
  }

  async function startCamera() {
    if (!navigator.mediaDevices?.getUserMedia) {
      setCameraState("error")
      setCameraMessage("Camera access is unavailable in this browser/context.")
      onNotify("Camera API unavailable. Use localhost or HTTPS.", "error")
      return
    }

    try {
      if (streamRef.current) stopCamera()
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: {
          width: { ideal: 960 },
          height: { ideal: 540 },
          facingMode: "environment",
        },
      })
      streamRef.current = stream
      if (videoRef.current) {
        videoRef.current.srcObject = stream
        await videoRef.current.play()
      }
      beginCalibration()
      onNotify("Camera active. Calibrating the normal scene.")
    } catch (cause) {
      setCameraState("error")
      setCameraMessage("Camera permission was denied or the camera is unavailable.")
      onNotify(
        cause instanceof Error ? cause.message : "Unable to start camera",
        "error",
      )
    }
  }

  function captureFrame(): FrameStats | null {
    const video = videoRef.current
    const canvas = canvasRef.current
    if (!video || !canvas || video.readyState < 2) return null

    const context = canvas.getContext("2d", { willReadFrequently: true })
    if (!context) return null

    canvas.width = GRID_W
    canvas.height = GRID_H

    // Focus on the central 80% of the frame so a person/object in front of the
    // station camera has more influence than minor motion near the edges.
    const sourceW = Math.max(1, video.videoWidth * 0.8)
    const sourceH = Math.max(1, video.videoHeight * 0.8)
    const sourceX = (video.videoWidth - sourceW) / 2
    const sourceY = (video.videoHeight - sourceH) / 2

    context.drawImage(
      video,
      sourceX,
      sourceY,
      sourceW,
      sourceH,
      0,
      0,
      GRID_W,
      GRID_H,
    )
    const image = context.getImageData(0, 0, GRID_W, GRID_H).data
    const gray = new Float32Array(GRID_W * GRID_H)
    for (let index = 0; index < gray.length; index += 1) {
      const offset = index * 4
      gray[index] =
        image[offset] * 0.299 + image[offset + 1] * 0.587 + image[offset + 2] * 0.114
    }
    return frameStats(gray)
  }

  async function reportDetection(type: "camera_obstruction" | "station_obstruction", confidence: number) {
    const now = Date.now()
    const last = lastReportedRef.current
    if (last && last.type === type && now - last.at < REPORT_COOLDOWN_MS) return
    lastReportedRef.current = { type, at: now }

    try {
      await api.createVisionObservation(
        selectedNode,
        [{ type, confidence: clamp01(confidence) }],
        "camera",
      )
      await refresh()
    } catch (cause) {
      onNotify(
        cause instanceof Error ? cause.message : "Vision observation failed",
        "error",
      )
    }
  }

  useEffect(() => {
    const interval = window.setInterval(() => {
      if (!streamRef.current || cameraState === "idle" || cameraState === "error") return
      const current = captureFrame()
      if (!current) return

      if (cameraState === "calibrating" || !baselineRef.current) {
        calibrationRef.current.push(current.pixels)
        setVisibilityScore(Math.round(clamp01((current.edge + current.stdev) / 45) * 100))
        if (calibrationRef.current.length >= CALIBRATION_FRAMES) {
          baselineRef.current = frameStats(averageFrames(calibrationRef.current))
          calibrationRef.current = []
          setCameraState("clear")
          setCameraMessage("Scene calibrated. Monitoring for blockage or a large foreground obstacle.")
        }
        return
      }

      const baseline = baselineRef.current
      let diffTotal = 0
      for (let index = 0; index < current.pixels.length; index += 1) {
        diffTotal += Math.abs(current.pixels[index] - baseline.pixels[index])
      }
      const normalizedDiff = diffTotal / Math.max(1, current.pixels.length) / 255
      const textureRatio = current.edge / Math.max(1.5, baseline.edge)
      const darkScore = clamp01((35 - current.mean) / 25)
      const flatScore = clamp01((0.45 - textureRatio) / 0.45)
      const blockScore = Math.max(darkScore, flatScore) * clamp01(normalizedDiff / 0.14)
      const obstacle = clamp01((normalizedDiff - 0.10) / 0.28)

      setObstructionScore(Math.round(Math.max(blockScore, obstacle) * 100))
      setVisibilityScore(
        Math.round(clamp01((current.edge + current.stdev) / Math.max(18, baseline.edge + baseline.stdev)) * 100),
      )

      const blockedCandidate = normalizedDiff > 0.10 && (current.mean < 24 || textureRatio < 0.32)
      const obstacleCandidate = normalizedDiff > 0.19 && !blockedCandidate

      blockedStreakRef.current = blockedCandidate ? blockedStreakRef.current + 1 : Math.max(0, blockedStreakRef.current - 1)
      obstacleStreakRef.current = obstacleCandidate ? obstacleStreakRef.current + 1 : Math.max(0, obstacleStreakRef.current - 1)
      clearStreakRef.current = !blockedCandidate && !obstacleCandidate ? clearStreakRef.current + 1 : 0

      if (blockedStreakRef.current >= 3) {
        if (cameraState !== "blocked") {
          setCameraState("blocked")
          setCameraMessage("Camera view is heavily obscured or has lost normal scene detail.")
          void reportDetection("camera_obstruction", Math.max(0.78, blockScore))
        }
        return
      }

      if (obstacleStreakRef.current >= 3) {
        if (cameraState !== "obstacle") {
          setCameraState("obstacle")
          setCameraMessage("Large foreground scene change detected in the monitored area.")
          void reportDetection("station_obstruction", Math.max(0.72, obstacle))
        }
        return
      }

      if (clearStreakRef.current >= 3 && cameraState !== "clear") {
        setCameraState("clear")
        setCameraMessage("Scene clear. Monitoring continues.")
      }
    }, ANALYZE_EVERY_MS)

    return () => window.clearInterval(interval)
  }, [cameraState, selectedNode])

  useEffect(() => () => stopCamera(), [])

  const detectorStatus =
    cameraState === "blocked"
      ? "critical"
      : cameraState === "obstacle"
        ? "warning"
        : cameraState === "clear"
          ? "healthy"
          : cameraState

  return (
    <div className="dashboard-view-stack">
      <div className="view-toolbar">
        <div>
          <p className="panel-eyebrow">Browser vision prototype</p>
          <h2>Vision Intelligence</h2>
          <span>
            Live camera scene-change and occlusion detection provides independent
            physical context. It never modifies weather sensor measurements.
          </span>
        </div>
      </div>

      <section className="glass-card live-vision-prototype">
        <PanelHeading
          eyebrow={`${displayNodeId(selectedNode)} camera`}
          title="Live obstruction detection"
          action={<StatusPill state={detectorStatus} />}
        />
        <div className="live-vision-layout">
          <div className="live-camera-stage">
            <video ref={videoRef} muted playsInline />
            <canvas ref={canvasRef} aria-hidden="true" />
            {!streamRef.current && (
              <div className="camera-empty-state">
                <Camera size={30} />
                <strong>Camera not started</strong>
                <span>Use localhost or HTTPS so the browser can request camera permission.</span>
              </div>
            )}
            {streamRef.current && (
              <div className={`camera-detection-badge ${cameraState}`}>
                {cameraState === "blocked" || cameraState === "obstacle" ? (
                  <TriangleAlert size={15} />
                ) : (
                  <ShieldCheck size={15} />
                )}
                {cameraState === "blocked"
                  ? "CAMERA BLOCKED"
                  : cameraState === "obstacle"
                    ? "OBSTACLE DETECTED"
                    : cameraState === "calibrating"
                      ? "CALIBRATING"
                      : "SCENE CLEAR"}
              </div>
            )}
          </div>

          <div className="vision-detector-panel">
            <div className="segmented-control compact">
              {nodes.map((nodeId) => (
                <button
                  key={nodeId}
                  className={selectedNode === nodeId ? "active" : ""}
                  onClick={() => setSelectedNode(nodeId)}
                >
                  {displayNodeId(nodeId)}
                </button>
              ))}
            </div>
            <div className="vision-score-grid">
              <div>
                <span>Obstruction score</span>
                <strong>{obstructionScore}%</strong>
              </div>
              <div>
                <span>Visual detail</span>
                <strong>{visibilityScore}%</strong>
              </div>
            </div>
            <p>{cameraMessage}</p>
            <div className="vision-command-row">
              {!streamRef.current ? (
                <button className="primary-command" onClick={() => void startCamera()}>
                  <Camera size={16} /> Start camera
                </button>
              ) : (
                <>
                  <button className="secondary-command" onClick={beginCalibration}>
                    <RefreshCw size={16} /> Recalibrate
                  </button>
                  <button className="secondary-command" onClick={stopCamera}>
                    <CameraOff size={16} /> Stop
                  </button>
                </>
              )}
            </div>
            <small>
              Prototype detector: calibrated frame differencing + image-detail checks with
              three-frame confirmation. It demonstrates camera blockage/foreground
              obstruction detection; it is not object recognition yet.
            </small>
          </div>
        </div>
      </section>

      <div className="vision-camera-grid">
        {nodes.map((nodeId) => {
          const status = statuses.find((item) => item.node_id === nodeId)
          const latest = status?.latest_analysis
          return (
            <section className="glass-card vision-camera-card" key={nodeId}>
              <PanelHeading
                eyebrow={`${displayNodeId(nodeId)} vision`}
                title="Camera context"
                action={<StatusPill state={status?.camera_status ?? "offline"} />}
              />
              <div className="vision-frame">
                <Camera size={26} />
                <strong>{latest ? titleCase(latest.type) : "No observation"}</strong>
                {latest?.source === "camera" && <span>LIVE CAMERA</span>}
              </div>
              <div className="vision-facts">
                <div>
                  <span>Context</span>
                  <strong>{latest ? titleCase(latest.event_group) : "Unavailable"}</strong>
                </div>
                <div>
                  <span>Confidence</span>
                  <strong>{latest ? `${Math.round(latest.confidence * 100)}%` : "—"}</strong>
                </div>
                <div>
                  <span>Last frame</span>
                  <strong>{formatRelativeTime(status?.last_observation_at)}</strong>
                </div>
              </div>
            </section>
          )
        })}
      </div>

      <section className="glass-card vision-timeline">
        <PanelHeading eyebrow="Visual context history" title="Vision event timeline" />
        {events.length ? (
          events.map((event, index) => (
            <article key={`${event.id ?? index}-${event.node_id}-${event.type}-${event.timestamp}`}>
              <time>{new Date(event.timestamp).toLocaleTimeString()}</time>
              <div>
                <strong>{titleCase(event.type)}</strong>
                <span>{displayNodeId(event.node_id)} · {event.message}</span>
              </div>
              <StatusPill state={event.severity} />
            </article>
          ))
        ) : (
          <EmptyState
            title="No visual observations"
            message="Start the camera and introduce an obstruction to create contextual vision events."
          />
        )}
      </section>
    </div>
  )
}
