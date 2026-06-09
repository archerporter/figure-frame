# Figure and Frame

**Movement analysis for PlatformPose data.**

Figure and Frame is a Flask web application that reads the `.db` files produced by [PlatformPose](https://github.com/archerporter/platformpose) and computes per-video movement metrics from the stored landmark data. It is designed to analyze the impact of vertical frame video composition on choreographic logics.

No additional data capture is required. Point Figure and Frame at a directory that already contains PlatformPose databases and it will analyse whatever is there.

---

## Features

- **Project grid** — home page lists all PlatformPose databases found, with video counts and frame totals
- **Metric table** — per-video distal/proximal ratio, X direction change rate, and level change rate, loaded asynchronously as you browse
- **Expandable charts** — click any row to expand an inline motion chart showing velocity and centre-of-mass height over time, with a scrubber
- **Video detail view** — full analysis page with a summary strip, motion chart, and optional skeleton panel synced to the chart playhead
- **Skeleton sync** — when PlatformPose is running, the video page fetches raw landmark frames via a local proxy and displays an animated skeleton alongside the chart; both respond to the same transport controls
- **Rule-based insights** — plain-English interpretation of each metric appears below the summary, flagging unusual values and noting data quality
- **Methodology note** — expandable panel on the video page explains how each metric is computed

---

## Requirements

- **Python 3.11 or later**
- One or more `.db` files produced by [PlatformPose](https://github.com/archerporter/platformpose)
- [PlatformPose](https://github.com/archerporter/platformpose) running at `localhost:5050` (optional — required only for skeleton sync on the video page)

Flask is the only third-party dependency.

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/archerporter/figure-frame.git
cd figure-frame
```

### 2. Create and activate a virtual environment

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

---

## Quick Start

By default, Figure and Frame looks for `.db` files in the current working directory. Run the server from wherever your PlatformPose databases live:

```bash
cd /path/to/your/data
python /path/to/figure-frame/app.py
```

Or set the `FF_DB_DIR` environment variable to point to the data directory from anywhere:

```bash
FF_DB_DIR=/path/to/your/data python app.py
```

The server launches in the background and returns the terminal immediately. The URL and process ID are printed at startup; the browser opens automatically after about one second.

```
Figure and Frame running at http://localhost:5051  (PID 12345)
Logs: /path/to/figure-frame/server.log
```

To stop the server, use the printed PID (`kill 12345`) or simply re-run the script — it kills any existing instance on port 5051 before starting a new one.

### Running alongside PlatformPose

If PlatformPose is also running (at `http://localhost:5050` by default), the video detail page will fetch raw skeleton frames via Figure and Frame's proxy endpoint and display an animated skeleton panel synced to the motion chart.

To use a non-default PlatformPose address:

```bash
PP_URL=http://localhost:5050 python app.py
```

---

## Interface

### Home

Lists all PlatformPose `.db` files found in `FF_DB_DIR` that contain a `frames` table. Each project card shows the video count, total frame count, and the most recently captured video. Click a card to open the project page.

### Project page

Shows a table of all videos in the project with:

| Column | Description |
|---|---|
| **Frames** | Number of clean (retained) frames |
| **Duration** | Session length in seconds |
| **Retention** | Percentage of attempted frames that passed the pose filter |
| **D/P ratio** | Median distal/proximal velocity ratio |
| **X changes /s** | X-axis direction reversals per second |
| **Level chg /s** | Centre-of-mass level changes per second |

Metric values are fetched asynchronously when the page loads. Click **▾** on any row to expand an inline motion chart with a time scrubber. Click **Analyze →** to open the full video detail page.

### Video page

The main analysis view for a single video:

- **Summary strip** — the three movement metrics plus duration and clean frame count at a glance
- **Insights** — rule-based interpretation of each metric value (see [Insights](#insights))
- **Motion chart** — two-track canvas chart (see [Motion Chart](#motion-chart))
- **Skeleton panel** — animated skeleton synced to the chart, loaded from PlatformPose if available; hidden otherwise
- **Transport bar** — shared play/pause, speed control (¼×, ½×, 1×, 2×), and scrubber driving both the chart playhead and the skeleton
- **Methodology note** — expandable explanation of how each metric is computed

---

## Motion Chart

The motion chart is a two-track canvas display drawn for each video.

**Velocity track (top)**

- **Purple line** — median speed of distal landmarks (wrists + ankles) in normalised frame units per second
- **Gray dashed line** — median speed of proximal landmarks (shoulders + hips)
- **Coral ticks** — moments of X-axis direction reversal

**Level track (bottom)**

- **Amber line** — centre-of-mass height above the estimated floor, with a filled area below

Click anywhere on the chart to seek to that time. The dashed playhead follows playback.

---

## Metrics

### Distal / Proximal ratio

At each frame, the mean speed of wrist (landmarks 15, 16) and ankle (landmarks 27, 28) landmarks is divided by the mean speed of shoulder (landmarks 11, 12) and hip (landmarks 23, 24) landmarks. The summary value is the **median** across all frames.

Values greater than 1× indicate that peripheral joints are moving faster than the body core. This is the central metric for the portrait-frame compensation hypothesis: a high D/P ratio is consistent with a dancer using active arm and leg gestures while keeping the torso relatively stable.

### X direction change rate

Sign reversals per second in the horizontal velocity of the centre of mass. A reversal is only counted when |v<sub>x</sub>| ≥ 0.08 (normalised units per second) to suppress noise and standing-still jitter.

### Level change rate

Local minima and maxima per second in centre-of-mass height above the estimated floor. The floor is computed as the 99th percentile of the maximum foot-landmark y-coordinate across all retained frames.

### A note on units

Landmark coordinates are MediaPipe normalised values (0–1 relative to frame dimensions). Velocities are therefore in frame-widths or frame-heights per second, not metric units. Values are comparable within a project but should not be compared across videos captured at different resolutions or with different capture regions.

---

## Insights

Each video analysis page includes a row of plain-English insights derived from the metric values.

| Level | Colour | Meaning |
|---|---|---|
| **info** | neutral | Metric falls within a normal or interpretable range |
| **warn** | amber | Metric suggests an unusual pattern worth noting |
| **flag** | red | Data quality concern — treat results with caution |

Thresholds:

| Metric | Threshold | Label |
|---|---|---|
| D/P ratio | < 0.8 | Warn — proximal joints faster than distal |
| D/P ratio | 0.8–1.2 | Balanced |
| D/P ratio | 1.2–1.8 | Moderate distal emphasis |
| D/P ratio | ≥ 1.8 | Strong distal articulation |
| X changes /s | < 0.15 | Predominantly linear path |
| X changes /s | 0.15–0.45 | Moderate lateral complexity |
| X changes /s | ≥ 0.45 | Active lateral movement |
| Level changes /s | < 2.0 | Flat vertical path |
| Level changes /s | 2.0–5.0 | Moderate vertical oscillation |
| Level changes /s | ≥ 5.0 | High vertical oscillation |
| Retention | < 50% | Flag — significant data loss |
| Retention | 50–70% | Warn — notable data loss |
| Retention | 70–85% | Warn — moderate data loss |

---

## API

Figure and Frame exposes a small JSON API used by the front end.

### `GET /api/analysis`

Full analysis for a single video, including the per-frame timeseries.

**Parameters:** `project`, `video_id`

**Response:**

```json
{
  "summary": {
    "distal_proximal_ratio": 1.42,
    "x_direction_change_rate": 0.31,
    "level_change_rate": 3.7
  },
  "insights": [
    { "level": "info", "text": "D/P ratio 1.42× — moderate distal emphasis..." }
  ],
  "retention_pct": 84.6,
  "duration_s": 47.2,
  "frame_count": 165,
  "floor_y": 0.91234,
  "timeseries": [
    { "t": 0.0, "distal_vel": null, "proximal_vel": null, "x_dir_change": false, "com_height": 0.312 },
    ...
  ]
}
```

### `GET /api/analysis-summary`

Same as `/api/analysis` but omits `timeseries`. Used by the project page to populate metric columns without loading full frame data for every video.

**Parameters:** `project`, `video_id`

### `GET /api/pp-frames`

Proxy for PlatformPose's `/api/frames` endpoint. Sidesteps browser cross-origin restrictions when both apps are running locally.

**Parameters:** `project`, `video_id`

Returns the PlatformPose response as-is, or a `502` error if PlatformPose is not reachable.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `FF_DB_DIR` | current working directory | Directory to scan for PlatformPose `.db` files |
| `PP_URL` | `http://localhost:5050` | URL of a running PlatformPose instance, used for cross-tool links and skeleton data |

---

## Research Context

Figure and Frame was developed as an analysis companion to PlatformPose. Where PlatformPose handles data capture — extracting landmark coordinates from screen recordings — Figure and Frame handles interpretation: turning the raw coordinate sequences into quantitative descriptions of how a dancer moves.

The three metrics operationalize specific predictions from the vertical frame compensation hypothesis (i.e., that dancers compensate for the limitations in frame with dynamic movements of their distal joints, as well as frequent horizontal direction changes). If dancers performing for short-form portrait video do adapt their movement to the constraints of vertical framing — emphasising gestures that remain visible in a tightly situated (or cropped) frame — the distal/proximal ratio should be elevated relative to equivalent performance in non-portrait contexts. The X direction change rate and level change rate offer complementary measures of spatial complexity within the frame.

Both tools are described in relation to each other in the [PlatformPose repository](https://github.com/archerporter/platformpose).

---

## Author

**L. Archer Porter**  
Researcher working at the intersection of digital humanities, dance and performance studies, and computational movement analysis.

---

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
