"""
Figure and Frame — movement analysis companion to PlatformPose.
Reads the same .db files produced by PlatformPose.

Usage:
    python app.py
    # or
    FLASK_APP=app.py flask run --port 5051

By default, Figure and Frame looks for .db files in the current working
directory. Set FF_DB_DIR to point to a different location:
    FF_DB_DIR=/path/to/data python app.py
"""
import os
import sys
import glob
import sqlite3
import math
import subprocess
import threading
import time
import webbrowser

from flask import Flask, render_template, request, jsonify, abort

_HERE  = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.environ.get('FF_DB_DIR', os.getcwd())

# Port at which PlatformPose is expected to run (used for cross-tool links)
PP_URL = os.environ.get('PP_URL', 'http://localhost:5050')

app = Flask(__name__, static_folder='static', template_folder='templates')
app.config['PP_URL'] = PP_URL


@app.context_processor
def _inject_globals():
    """Make pp_url available in every template without explicit passing."""
    return {'pp_url': PP_URL}


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _db_path(project: str) -> str:
    return os.path.join(DB_DIR, f'{project}.db')


def _list_projects():
    """Return sorted list of project names (*.db files in DB_DIR that have a frames table)."""
    projects = []
    for path in sorted(glob.glob(os.path.join(DB_DIR, '*.db'))):
        name = os.path.splitext(os.path.basename(path))[0]
        try:
            with sqlite3.connect(path) as conn:
                tables = {r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
            if 'frames' in tables:
                projects.append(name)
        except Exception:
            pass
    return projects


def _corpus_summary(project: str):
    """Return list of video dicts with frame counts and retention info."""
    db = _db_path(project)
    if not os.path.exists(db):
        return []
    with sqlite3.connect(db) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if 'frames' not in tables:
            return []
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT
                video_id,
                COUNT(*)                                       AS frames,
                ROUND(MAX(timestamp), 2)                       AS duration_s,
                MIN(captured_at)                               AS first_seen,
                MAX(frame) + 1                                 AS total_attempted,
                ROUND(COUNT(*) * 100.0 / (MAX(frame) + 1), 1) AS retention_pct
            FROM frames
            GROUP BY video_id
            ORDER BY MIN(captured_at) DESC
        """).fetchall()
    return [dict(r) for r in rows]


# ── Timestamp stitching ────────────────────────────────────────────────────────

def _stitch_timestamps(rows):
    """
    Add a monotonic `t` field to each row dict by stitching multiple
    capture sessions.  A new session is detected when timestamp resets
    (t_raw < prev_raw - 1.0); the offset is advanced by the previous
    session's max timestamp so time runs continuously.
    """
    offset       = 0.0
    prev_raw     = -1.0
    session_max  = 0.0
    result       = []
    for row in rows:
        t_raw = row.get('timestamp') or 0.0
        if prev_raw >= 0 and t_raw < prev_raw - 1.0:
            offset      += session_max + 0.2   # small gap between sessions
            session_max  = 0.0
        row['t']    = round(offset + t_raw, 4)
        session_max = max(session_max, t_raw)
        prev_raw    = t_raw
        result.append(row)
    return result


# ── Analysis ───────────────────────────────────────────────────────────────────

# Landmark groups
DISTAL_LMS   = (15, 16, 27, 28)   # wrists + ankles
PROXIMAL_LMS = (11, 12, 23, 24)   # shoulders + hips
FOOT_LMS     = (27, 28, 29, 30, 31, 32)

X_DIR_THRESHOLD = 0.08   # min |vx| to count as directional intent


def _group_centroid(row, idxs, vis_min=0.3):
    xs = [row[f'lm{i}_x'] for i in idxs
          if (row.get(f'lm{i}_vis') or 0) > vis_min]
    ys = [row[f'lm{i}_y'] for i in idxs
          if (row.get(f'lm{i}_vis') or 0) > vis_min]
    if not xs:
        return None, None
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _generate_insights(summary, retention_pct=None):
    """
    Interpret a video's summary metrics as plain-English leads.

    Returns a list of dicts with keys:
        level — 'info' | 'warn' | 'flag'
        text  — one sentence
    """
    insights = []
    dp   = summary.get('distal_proximal_ratio')
    xdir = summary.get('x_direction_change_rate')
    lc   = summary.get('level_change_rate')

    # ── Distal / proximal ratio ────────────────────────────────────────────────
    if dp is not None:
        if dp < 0.8:
            insights.append({'level': 'warn',
                'text': f'D/P ratio {dp}× — proximal joints moving faster than peripheral '
                        f'ones; unusual for dance, may indicate whole-body translation or '
                        f'restrained limb use.'})
        elif dp < 1.2:
            insights.append({'level': 'info',
                'text': f'D/P ratio {dp}× — balanced movement: peripheral and core joints '
                        f'travelling at similar speeds.'})
        elif dp < 1.8:
            insights.append({'level': 'info',
                'text': f'D/P ratio {dp}× — moderate distal emphasis: wrists and ankles '
                        f'moving somewhat faster than the body core.'})
        else:
            insights.append({'level': 'info',
                'text': f'D/P ratio {dp}× — strong distal articulation: peripheral joints '
                        f'moving roughly {dp}× faster than shoulders and hips, consistent '
                        f'with active arm and leg use.'})

    # ── X-direction change rate ────────────────────────────────────────────────
    if xdir is not None:
        if xdir < 0.15:
            insights.append({'level': 'info',
                'text': f'X changes {xdir}/s — predominantly linear or stationary '
                        f'horizontal path; limited lateral back-and-forth movement.'})
        elif xdir < 0.45:
            insights.append({'level': 'info',
                'text': f'X changes {xdir}/s — moderate lateral complexity; '
                        f'occasional horizontal direction shifts across the frame.'})
        else:
            insights.append({'level': 'info',
                'text': f'X changes {xdir}/s — active lateral movement; frequent '
                        f'horizontal direction reversals across the frame.'})

    # ── Level change rate ──────────────────────────────────────────────────────
    if lc is not None:
        if lc < 2.0:
            insights.append({'level': 'info',
                'text': f'Level changes {lc}/s — relatively flat vertical path; '
                        f'limited rising and falling through the frame.'})
        elif lc < 5.0:
            insights.append({'level': 'info',
                'text': f'Level changes {lc}/s — moderate vertical oscillation; '
                        f'regular changes in center-of-mass elevation.'})
        else:
            insights.append({'level': 'info',
                'text': f'Level changes {lc}/s — high vertical oscillation; '
                        f'frequent and rapid changes in center-of-mass height.'})

    # ── Data quality ───────────────────────────────────────────────────────────
    if retention_pct is not None:
        if retention_pct < 50:
            insights.append({'level': 'flag',
                'text': f'{retention_pct}% frame retention — high data loss; pose '
                        f'estimation struggled significantly with this video. '
                        f'Treat all metrics with caution.'})
        elif retention_pct < 70:
            insights.append({'level': 'warn',
                'text': f'{retention_pct}% frame retention — notable data loss; '
                        f'some movement sequences may be underrepresented in the analysis.'})
        elif retention_pct < 85:
            insights.append({'level': 'warn',
                'text': f'{retention_pct}% frame retention — moderate data loss; '
                        f'results are generally reliable but worth noting.'})

    return insights


def _analyze_video(project: str, video_id: str, summary_only: bool = False):
    """
    Load frames and compute analysis.

    Returns dict with keys:
        timeseries  — list of per-frame metrics (omitted when summary_only=True)
        summary     — {distal_proximal_ratio, x_direction_change_rate, level_change_rate}
        duration_s
        frame_count
        floor_y
    Returns None if no data found.
    """
    db = _db_path(project)
    if not os.path.exists(db):
        return None

    with sqlite3.connect(db) as _chk:
        tables = {r[0] for r in _chk.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    if 'frames' not in tables:
        return None

    lm_cols = ', '.join(
        f'lm{i}_{c}'
        for i in range(33)
        for c in ('x', 'y', 'z', 'vis')
    )
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT frame, timestamp, {lm_cols} "
            f"FROM frames WHERE video_id=? ORDER BY id",
            (video_id,)
        ).fetchall()

    if not rows:
        return None

    rows = [dict(r) for r in rows]
    rows = _stitch_timestamps(rows)

    # Retention: clean frames vs total attempted (MAX(frame)+1 within this video_id)
    max_frame     = max(r['frame'] for r in rows)
    retention_pct = round(len(rows) * 100.0 / (max_frame + 1), 1)

    # Floor: 99th-percentile of max foot-landmark y across all frames
    foot_ys = sorted(
        max(r.get(f'lm{i}_y') or 0.0 for i in FOOT_LMS)
        for r in rows
    )
    p99      = foot_ys[min(int(0.99 * len(foot_ys)), len(foot_ys) - 1)]
    floor_y  = p99

    duration_s = rows[-1]['t'] if rows else 0.0

    # ── Per-frame timeseries ─────────────────────────────────────────────────
    timeseries   = []
    x_dir_chgs   = 0
    level_chgs   = 0
    prev_com_x   = None
    prev_com_dir = 0

    dp_ratios    = []

    for i, row in enumerate(rows):
        t   = row['t']
        cx, cy = _group_centroid(row, (11, 12, 23, 24))   # CoM = shoulders+hips
        com_height = round(floor_y - cy, 4) if cy is not None else None

        distal_vel  = None
        proximal_vel = None
        x_dir_change = False

        if i > 0:
            prev = rows[i - 1]
            dt   = t - prev['t']
            if dt >= 0.05:   # ignore sub-50ms gaps (duplicate timestamps, session seams)
                # Distal velocity (wrists + ankles)
                d_speeds = []
                for idx in DISTAL_LMS:
                    if (row.get(f'lm{idx}_vis') or 0) > 0.3:
                        dx = (row[f'lm{idx}_x'] - prev[f'lm{idx}_x']) / dt
                        dy = (row[f'lm{idx}_y'] - prev[f'lm{idx}_y']) / dt
                        d_speeds.append(math.hypot(dx, dy))
                if d_speeds:
                    distal_vel = round(sum(d_speeds) / len(d_speeds), 4)

                # Proximal velocity (shoulders + hips)
                p_speeds = []
                for idx in PROXIMAL_LMS:
                    if (row.get(f'lm{idx}_vis') or 0) > 0.3:
                        dx = (row[f'lm{idx}_x'] - prev[f'lm{idx}_x']) / dt
                        dy = (row[f'lm{idx}_y'] - prev[f'lm{idx}_y']) / dt
                        p_speeds.append(math.hypot(dx, dy))
                if p_speeds:
                    proximal_vel = round(sum(p_speeds) / len(p_speeds), 4)

                # Distal/proximal ratio sample
                if distal_vel is not None and proximal_vel and proximal_vel > 0:
                    dp_ratios.append(distal_vel / proximal_vel)

                # X direction change (based on CoM x velocity)
                if cx is not None and prev_com_x is not None:
                    vx = (cx - prev_com_x) / dt
                    if abs(vx) >= X_DIR_THRESHOLD:
                        new_dir = 1 if vx > 0 else -1
                        if prev_com_dir != 0 and new_dir != prev_com_dir:
                            x_dir_change = True
                            x_dir_chgs  += 1
                        prev_com_dir = new_dir

        prev_com_x = cx

        timeseries.append({
            't':            t,
            'distal_vel':   distal_vel,
            'proximal_vel': proximal_vel,
            'x_dir_change': x_dir_change,
            'com_height':   com_height,
        })

    # Level changes: local extrema in CoM height
    heights = [p['com_height'] for p in timeseries if p['com_height'] is not None]
    for j in range(1, len(heights) - 1):
        is_max = heights[j] > heights[j-1] and heights[j] > heights[j+1]
        is_min = heights[j] < heights[j-1] and heights[j] < heights[j+1]
        if is_max or is_min:
            level_chgs += 1

    # Summary stats
    dp_ratio = round(sorted(dp_ratios)[len(dp_ratios) // 2], 3) if dp_ratios else None
    x_rate   = round(x_dir_chgs / duration_s, 2) if duration_s > 0 else None
    lc_rate  = round(level_chgs / duration_s, 2) if duration_s > 0 else None

    summary = {
        'distal_proximal_ratio':   dp_ratio,
        'x_direction_change_rate': x_rate,
        'level_change_rate':       lc_rate,
    }
    result = {
        'summary':       summary,
        'insights':      _generate_insights(summary, retention_pct),
        'retention_pct': retention_pct,
        'duration_s':    round(duration_s, 3),
        'frame_count':   len(rows),
        'floor_y':       round(floor_y, 5),
    }
    if not summary_only:
        result['timeseries'] = timeseries
    return result


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route('/about')
def about():
    return render_template('about.html')


@app.route('/')
def index():
    projects = _list_projects()
    corpora  = {p: _corpus_summary(p) for p in projects}
    return render_template('index.html', projects=projects, corpora=corpora)


@app.route('/project/<project>')
def project_view(project):
    if not os.path.exists(_db_path(project)):
        abort(404)
    summary = _corpus_summary(project)
    return render_template('project.html',
                           project=project,
                           summary=summary,
                           pp_url=PP_URL)


@app.route('/video/<project>/<video_id>')
def video_view(project, video_id):
    if not os.path.exists(_db_path(project)):
        abort(404)
    return render_template('video.html',
                           project=project,
                           video_id=video_id,
                           pp_url=PP_URL)


@app.route('/api/analysis')
def api_analysis():
    project  = request.args.get('project', '').strip()
    video_id = request.args.get('video_id', '').strip()
    if not project or not video_id:
        return jsonify({'error': 'project and video_id required'}), 400
    result = _analyze_video(project, video_id)
    if result is None:
        return jsonify({'error': 'no data found'}), 404
    return jsonify(result)


@app.route('/api/pp-frames')
def pp_frames_proxy():
    """Proxy PlatformPose /api/frames — sidesteps browser cross-origin restrictions."""
    import urllib.request, urllib.parse, urllib.error
    project  = request.args.get('project', '').strip()
    video_id = request.args.get('video_id', '').strip()
    if not project or not video_id:
        return jsonify({'error': 'project and video_id required'}), 400
    params = urllib.parse.urlencode({'project': project, 'video_id': video_id})
    url = f'{PP_URL}/api/frames?{params}'
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            body = resp.read()
        return body, 200, {'Content-Type': 'application/json'}
    except urllib.error.HTTPError as e:
        return jsonify({'error': f'PlatformPose returned {e.code}: {e.reason}'}), e.code
    except Exception as e:
        return jsonify({'error': f'Could not reach PlatformPose at {PP_URL} — is it running?'}), 502


@app.route('/api/analysis-summary')
def api_analysis_summary():
    """Returns only the summary dict — faster than full timeseries."""
    project  = request.args.get('project', '').strip()
    video_id = request.args.get('video_id', '').strip()
    if not project or not video_id:
        return jsonify({'error': 'project and video_id required'}), 400
    result = _analyze_video(project, video_id, summary_only=True)
    if result is None:
        return jsonify({'error': 'no data found'}), 404
    return jsonify(result)


def _free_port(port: int):
    """
    Kill any process bound to *port*, then wait until the socket is actually
    released before returning.  SIGTERM is too slow on macOS — use SIGKILL and
    poll until a connect attempt is refused (up to 3 s).
    """
    import signal, socket, time, subprocess as _sp

    try:
        result = _sp.run(['lsof', '-ti', f':{port}'],
                         capture_output=True, text=True)
        pids = [int(p) for p in result.stdout.split() if p.strip()]
    except Exception:
        pids = []

    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    if not pids:
        return

    deadline = time.time() + 3.0
    while time.time() < deadline:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.1):
                pass
        except (ConnectionRefusedError, OSError):
            return
        time.sleep(0.1)


if __name__ == '__main__':
    PORT = 5051

    if '--_server-mode' in sys.argv:
        # Background child — just run Flask
        app.run(host='127.0.0.1', port=PORT, debug=False)
    else:
        # Launcher: free port, spawn detached child, open browser, exit
        _free_port(PORT)
        log_path = os.path.join(_HERE, 'server.log')
        with open(log_path, 'w') as _log:
            child = subprocess.Popen(
                [sys.executable, os.path.abspath(__file__), '--_server-mode'],
                stdout=_log,
                stderr=_log,
                start_new_session=True,
            )
        print(f'Figure and Frame running at http://localhost:{PORT}  (PID {child.pid})')
        print(f'Logs: {log_path}')
        time.sleep(0.9)
        webbrowser.open(f'http://localhost:{PORT}')
