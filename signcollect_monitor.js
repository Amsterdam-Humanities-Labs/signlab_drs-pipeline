/**
 * SignCollect Client Monitor Library for Node.js
 *
 * A reusable library for sending heartbeat/monitoring data to the SignCollect server.
 * Allows services to register themselves and send periodic heartbeats with optional status information.
 */

const https = require('https');
const http = require('http');
const os = require('os');

const DEFAULT_API_URL = 'https://signcollect.nl/client_monitor_api/api.php';

class SignCollectMonitor {
  /**
   * Initialize the SignCollect monitor client.
   *
   * @param {string} clientId - Unique identifier for this client (e.g., 'drs-server')
   * @param {string} clientName - Human-readable name for this client (e.g., 'DRS Express Server')
   * @param {string} [description=''] - Optional description of what this client does
   * @param {number} [heartbeatInterval=3600] - Expected interval between heartbeats in seconds
   * @param {string} [apiUrl=DEFAULT_API_URL] - API endpoint URL
   */
  constructor(clientId, clientName, description = '', heartbeatInterval = 3600, apiUrl = DEFAULT_API_URL) {
    this.clientId = clientId;
    this.clientName = clientName;
    this.description = description;
    this.heartbeatInterval = heartbeatInterval;
    this.apiUrl = apiUrl;
    this.heartbeatTimer = null;
  }

  /**
   * Get the current machine's hostname.
   * @returns {string}
   */
  _getHostname() {
    try {
      return os.hostname();
    } catch (e) {
      return 'unknown';
    }
  }

  /**
   * Make a POST request to the API.
   *
   * @param {string} action - The API action to call
   * @param {Object} data - The POST data to send
   * @returns {Promise<Object>} - The JSON response from the API or error object
   */
  _makeRequest(action, data) {
    return new Promise((resolve) => {
      const url = new URL(this.apiUrl);
      url.searchParams.set('action', action);

      const postData = JSON.stringify(data);
      const isHttps = url.protocol === 'https:';
      const lib = isHttps ? https : http;

      const options = {
        hostname: url.hostname,
        port: url.port || (isHttps ? 443 : 80),
        path: url.pathname + url.search,
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(postData)
        },
        timeout: 30000,
        rejectUnauthorized: false // Allow self-signed certs
      };

      const req = lib.request(options, (res) => {
        let body = '';
        res.on('data', chunk => body += chunk);
        res.on('end', () => {
          try {
            const result = JSON.parse(body);
            resolve(result);
          } catch (e) {
            console.error(`Error decoding JSON response: ${e.message}`);
            resolve({ error: `Invalid JSON response: ${e.message}`, success: false });
          }
        });
      });

      req.on('error', (e) => {
        console.error(`Error making request to monitor API: ${e.message}`);
        resolve({ error: e.message, success: false });
      });

      req.on('timeout', () => {
        req.destroy();
        console.error('Request to monitor API timed out');
        resolve({ error: 'Request timeout', success: false });
      });

      req.write(postData);
      req.end();
    });
  }

  /**
   * Register this client with the monitoring system.
   *
   * @param {Object} [metadata=null] - Optional additional metadata to include
   * @returns {Promise<Object>} - API response with success status
   */
  async register(metadata = null) {
    const data = {
      client_id: this.clientId,
      client_name: this.clientName,
      description: this.description,
      heartbeat_interval: this.heartbeatInterval,
      hostname: this._getHostname()
    };

    if (metadata) {
      data.metadata = JSON.stringify(metadata);
    }

    const result = await this._makeRequest('register', data);

    if (result.success) {
      console.log(`Client '${this.clientName}' registered successfully`);
    } else {
      const errorMsg = result.error || result.message || 'Unknown error';
      console.error(`Failed to register client '${this.clientName}': ${errorMsg}`);
    }

    return result;
  }

  /**
   * Send a heartbeat to update the last_seen timestamp.
   *
   * @param {Object} [metadata=null] - Optional additional metadata to include
   * @returns {Promise<Object>} - API response with success status
   */
  async sendHeartbeat(metadata = null) {
    const data = {
      client_id: this.clientId
    };

    if (metadata) {
      data.metadata = JSON.stringify(metadata);
    }

    const result = await this._makeRequest('heartbeat', data);

    if (result.success) {
      console.log(`Heartbeat sent for '${this.clientName}'`);
    } else {
      const errorMsg = result.error || result.message || 'Unknown error';
      console.error(`Failed to send heartbeat for '${this.clientName}': ${errorMsg}`);
    }

    return result;
  }

  /**
   * Send a heartbeat with status information and statistics.
   *
   * This method sends a regular heartbeat with the stats, status, and message
   * included as metadata.
   *
   * @param {string} status - Status string (e.g., 'success', 'warning', 'error')
   * @param {string} message - Human-readable status message
   * @param {Object} [stats=null] - Optional dictionary of statistics/metrics
   * @returns {Promise<Object>} - API response with success status
   */
  async sendHeartbeatWithStats(status, message, stats = null) {
    const metadata = {
      status: status,
      message: message,
      timestamp: new Date().toISOString()
    };

    if (stats) {
      metadata.stats = stats;
    }

    return this.sendHeartbeat(metadata);
  }

  /**
   * Start automatic heartbeat sending at the configured interval.
   * @param {Object} [metadata=null] - Optional metadata to include with each heartbeat
   */
  startAutoHeartbeat(metadata = null) {
    if (this.heartbeatTimer) {
      console.log('Auto heartbeat already running');
      return;
    }

    // Send initial heartbeat
    this.sendHeartbeat(metadata);

    // Schedule periodic heartbeats
    this.heartbeatTimer = setInterval(() => {
      this.sendHeartbeat(metadata);
    }, this.heartbeatInterval * 1000);

    console.log(`Auto heartbeat started (interval: ${this.heartbeatInterval}s)`);
  }

  /**
   * Stop automatic heartbeat sending.
   */
  stopAutoHeartbeat() {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
      console.log('Auto heartbeat stopped');
    }
  }
}

module.exports = { SignCollectMonitor, DEFAULT_API_URL };

// Example usage and testing
if (require.main === module) {
  (async () => {
    // Create a test monitor instance
    const monitor = new SignCollectMonitor(
      'test-client-node',
      'Test Client Node.js',
      'A test client for verifying the monitoring system from Node.js',
      3600
    );

    // Test registration
    console.log('Testing registration...');
    const regResult = await monitor.register({ version: '1.0.0', platform: 'nodejs' });
    console.log('Registration result:', regResult);
    console.log();

    // Test basic heartbeat
    console.log('Testing basic heartbeat...');
    const hbResult = await monitor.sendHeartbeat();
    console.log('Heartbeat result:', hbResult);
    console.log();

    // Test heartbeat with stats
    console.log('Testing heartbeat with stats...');
    const statsResult = await monitor.sendHeartbeatWithStats(
      'success',
      'Test completed successfully',
      {
        processed_requests: 42,
        uptime_seconds: 3600,
        active_connections: 5
      }
    );
    console.log('Heartbeat with stats result:', statsResult);
  })();
}
