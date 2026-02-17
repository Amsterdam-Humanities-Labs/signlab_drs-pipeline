<?php
// drs_ep/api.php
// Render coordination API - JSON file based (no database needed)
// Drop this file in signcollect.nl/drs_ep/api.php and it works.

header('Content-Type: application/json');

$DATA_FILE = __DIR__ . '/render_claims.json';

// ── File-based storage with locking ────────────────────────────────────

function load_data($file) {
    if (!file_exists($file)) return [];
    $json = file_get_contents($file);
    return json_decode($json, true) ?: [];
}

function save_data($file, $data) {
    file_put_contents($file, json_encode($data, JSON_PRETTY_PRINT));
}

/**
 * Run a callback with exclusive file lock.
 * Callback receives current data array, returns modified data array.
 */
function with_lock($file, $callback) {
    $lockfile = $file . '.lock';
    $fp = fopen($lockfile, 'w');
    if (!flock($fp, LOCK_EX)) {
        fclose($fp);
        return ['success' => false, 'message' => 'Could not acquire lock'];
    }
    try {
        $data = load_data($file);
        $result = $callback($data);
        if (isset($result['_data'])) {
            save_data($file, $result['_data']);
            unset($result['_data']);
        }
        flock($fp, LOCK_UN);
        fclose($fp);
        return $result;
    } catch (Exception $e) {
        flock($fp, LOCK_UN);
        fclose($fp);
        return ['success' => false, 'message' => $e->getMessage()];
    }
}

// ── Route ──────────────────────────────────────────────────────────────

$action = $_GET['action'] ?? '';
$input = json_decode(file_get_contents('php://input'), true) ?? [];

switch ($action) {

case 'claim':
    $filename = $input['filename'] ?? '';
    $machine = $input['machine'] ?? '';
    if (!$filename || !$machine) { echo json_encode(['success' => false, 'message' => 'Missing filename or machine']); break; }

    echo json_encode(with_lock($DATA_FILE, function($data) use ($filename, $machine) {
        foreach ($data as $row) {
            if ($row['filename'] === $filename && $row['status'] === 'claimed') {
                return ['success' => false, 'message' => 'Already claimed by ' . $row['machine'], 'claimed_by' => $row['machine']];
            }
        }
        $data[] = ['filename' => $filename, 'machine' => $machine, 'status' => 'claimed', 'claimed_at' => date('Y-m-d H:i:s')];
        return ['success' => true, 'message' => 'File claimed', '_data' => $data];
    }));
    break;

case 'claim_batch':
    $filenames = $input['filenames'] ?? [];
    $machine = $input['machine'] ?? '';
    if (!$filenames || !$machine) { echo json_encode(['success' => false, 'message' => 'Missing filenames or machine']); break; }

    echo json_encode(with_lock($DATA_FILE, function($data) use ($filenames, $machine) {
        // Build set of currently claimed filenames
        $active = [];
        foreach ($data as $row) {
            if ($row['status'] === 'claimed') $active[$row['filename']] = true;
        }

        $claimed = [];
        $already_claimed = [];
        $now = date('Y-m-d H:i:s');

        foreach ($filenames as $f) {
            if (isset($active[$f])) {
                $already_claimed[] = $f;
            } else {
                $data[] = ['filename' => $f, 'machine' => $machine, 'status' => 'claimed', 'claimed_at' => $now];
                $active[$f] = true;
                $claimed[] = $f;
            }
        }
        return ['success' => true, 'claimed' => $claimed, 'already_claimed' => $already_claimed, '_data' => $data];
    }));
    break;

case 'complete':
    $filename = $input['filename'] ?? '';
    $machine = $input['machine'] ?? '';

    echo json_encode(with_lock($DATA_FILE, function($data) use ($filename, $machine) {
        $found = false;
        $now = date('Y-m-d H:i:s');
        foreach ($data as &$row) {
            if ($row['filename'] === $filename && $row['status'] === 'claimed') {
                $row['status'] = 'completed';
                $row['completed_at'] = $now;
                $found = true;
                break;
            }
        }
        return ['success' => $found, 'message' => $found ? 'File marked as completed' : 'No active claim found', '_data' => $data];
    }));
    break;

case 'complete_batch':
    $filenames = $input['filenames'] ?? [];

    echo json_encode(with_lock($DATA_FILE, function($data) use ($filenames) {
        $set = array_flip($filenames);
        $count = 0;
        $now = date('Y-m-d H:i:s');
        foreach ($data as &$row) {
            if (isset($set[$row['filename']]) && $row['status'] === 'claimed') {
                $row['status'] = 'completed';
                $row['completed_at'] = $now;
                $count++;
            }
        }
        return ['success' => true, 'completed' => $count, '_data' => $data];
    }));
    break;

case 'release':
    $filename = $input['filename'] ?? '';

    echo json_encode(with_lock($DATA_FILE, function($data) use ($filename) {
        $data = array_values(array_filter($data, function($row) use ($filename) {
            return !($row['filename'] === $filename && $row['status'] === 'claimed');
        }));
        return ['success' => true, 'message' => 'Claim released', '_data' => $data];
    }));
    break;

case 'release_batch':
    $filenames = $input['filenames'] ?? [];

    echo json_encode(with_lock($DATA_FILE, function($data) use ($filenames) {
        $set = array_flip($filenames);
        $before = count($data);
        $data = array_values(array_filter($data, function($row) use ($set) {
            return !(isset($set[$row['filename']]) && $row['status'] === 'claimed');
        }));
        return ['success' => true, 'released' => $before - count($data), '_data' => $data];
    }));
    break;

case 'get_active':
    $data = load_data($DATA_FILE);
    $claims = array_values(array_filter($data, function($r) { return $r['status'] === 'claimed'; }));
    // Return only relevant fields
    $claims = array_map(function($r) { return ['filename' => $r['filename'], 'machine' => $r['machine'], 'claimed_at' => $r['claimed_at']]; }, $claims);
    echo json_encode(['success' => true, 'claims' => $claims]);
    break;

case 'check':
    $filename = $_GET['filename'] ?? '';
    $data = load_data($DATA_FILE);
    foreach ($data as $row) {
        if ($row['filename'] === $filename && $row['status'] === 'claimed') {
            echo json_encode(['success' => true, 'claimed' => true, 'claimed_by' => $row['machine'], 'claimed_at' => $row['claimed_at']]);
            exit;
        }
    }
    echo json_encode(['success' => true, 'claimed' => false]);
    break;

case 'get_completed':
    $date = $_GET['date'] ?? null;
    $data = load_data($DATA_FILE);
    $files = array_values(array_filter($data, function($r) use ($date) {
        if ($r['status'] !== 'completed') return false;
        if ($date) {
            $dateCompact = str_replace('-', '', $date);
            return strpos($r['filename'], $dateCompact) !== false;
        }
        return true;
    }));
    $files = array_map(function($r) { return ['filename' => $r['filename'], 'machine' => $r['machine'], 'completed_at' => $r['completed_at'] ?? '']; }, $files);
    echo json_encode(['success' => true, 'files' => $files]);
    break;

case 'stats':
    $data = load_data($DATA_FILE);
    $stats = [];
    foreach ($data as $row) {
        $m = $row['machine'];
        if (!isset($stats[$m])) $stats[$m] = ['claimed' => 0, 'completed' => 0];
        if ($row['status'] === 'claimed') $stats[$m]['claimed']++;
        if ($row['status'] === 'completed') $stats[$m]['completed']++;
    }
    echo json_encode(['success' => true, 'stats' => $stats]);
    break;

case 'cleanup':
    $maxAge = $input['max_age_minutes'] ?? 60;

    echo json_encode(with_lock($DATA_FILE, function($data) use ($maxAge) {
        $cutoff = strtotime("-{$maxAge} minutes");
        $before = count($data);
        $data = array_values(array_filter($data, function($row) use ($cutoff) {
            if ($row['status'] !== 'claimed') return true;
            return strtotime($row['claimed_at']) >= $cutoff;
        }));
        $released = $before - count($data);
        return ['success' => true, 'released' => $released, 'message' => "Released {$released} stale claims", '_data' => $data];
    }));
    break;

default:
    echo json_encode(['success' => false, 'message' => "Unknown action: $action"]);
}
