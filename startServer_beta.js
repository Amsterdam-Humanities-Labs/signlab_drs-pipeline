const http = require('http');
const fs = require('fs');
const path = require('path');
const cors = require('cors'); // Import the CORS middleware
const bodyParser = require('body-parser');
const WebSocket = require('ws');
const express = require('express');
const { exec } = require('child_process'); // Import exec from child_process
const https = require('https');
const axios = require('axios');
const FormData = require('form-data');
const { SignCollectMonitor } = require('./signcollect_monitor.js');

// Initialize the monitor
const monitor = new SignCollectMonitor(
  'drs-express-server',
  'DRS Express Server',
  'Camera communication server with WebSocket support',
  3600
);

// Register with monitoring system and start auto-heartbeat
monitor.register().then(() => {
  monitor.startAutoHeartbeat();
});

var ftpBusy = false;
https.globalAgent.options.rejectUnauthorized = false;

// Directory to serve files from
const directoryToServe = '/Users/admin/signCollect/studioFiles';

// Initialize Express app
const server = express();
const server_ws = http.createServer(server);

// Create a WebSocket server on port 8081
const wss = new WebSocket.Server({ port: 8081 });

// Middleware Setup
server.use(cors()); // Enable CORS for all routes
server.use(express.static(directoryToServe)); // Serve static files

// Middleware to log incoming requests
server.use((req, res, next) => {
  console.log(`Incoming Request: ${req.method} ${req.originalUrl}`);
  next(); // Pass control to the next middleware or route handler
});

// Body Parser Middleware to parse JSON and URL-encoded data
server.use(bodyParser.json());
server.use(bodyParser.urlencoded({ extended: true }));

// Helper Function to Get the Latest File in the Directory
function getLatestFile(directoryPath) {
  const files = fs.readdirSync(directoryPath);
  const fileStats = files.map((file) => {
    const filePath = path.join(directoryPath, file);
    return { file, stats: fs.statSync(filePath) };
  });

  const latestFile = fileStats.reduce((prev, current) =>
    prev.stats.mtime > current.stats.mtime ? prev : current
  );

  console.log(`Latest File: ${latestFile.file}`);
  return latestFile.file;
}

// Queue to hold conversion tasks
const conversionQueue = [];

// Flag to indicate if a conversion is in progress
let isProcessing = false;

// /**
//  * Converts a video file using FFmpeg and generates a thumbnail.
//  * @param {string} filePath - The full path of the file to convert.
//  * @returns {Promise<void>}
//  */
// function convertAndGenerateThumbnail(filePath) {
//   return new Promise((resolve, reject) => {
//     // Extract the filename from the full path
//     const filename = path.basename(filePath); // One-liner to get filename

//     // Validate filename format: starts with B, A, M, L, R followed by 8-digit date and 4-digit identifier
//     const regex = /^[BAMLR]\d{8}_\d{4}\.MP4$/i;
//     if (!regex.test(filename)) {
//       return reject(new Error('Invalid filename format.'));
//     }

//     // // Extract date from filename (e.g., 20241024)
//     // const dateMatch = filename.match(/^[BAMLR](\d{8})_/);
//     // if (!dateMatch) {
//     //   return reject(new Error('Date not found in filename.'));
//     // }

//   //generate date from today, so not the filename
    
//     const today = new Date();
//     const year = today.getFullYear();
//     const month = String(today.getMonth() + 1).padStart(2, '0'); // Months are zero-based
//     const day = String(today.getDate()).padStart(2, '0'); // Pad day with leading zero
//     const dateStr = `${year}${month}${day}`; // e.g., '20241024'


//     // const dateStr = dateMatch[1]; // e.g., '20241024'

//     // Format date to 'YYYY-MM-DD'
//     const formattedDate = `${dateStr.slice(0, 4)}-${dateStr.slice(4, 6)}-${dateStr.slice(6, 8)}`;

//     // Define directories
//     const rawDir = path.join(directoryToServe, `${formattedDate}/raw`);
//     const convertedDir = path.join(directoryToServe, `${formattedDate}/converted`);
//     const thumbnailDir = path.join(directoryToServe, `${formattedDate}/thumbnails`); // Directory for thumbnails

//     // Define source and destination file paths
//     const sourcePath = path.join(rawDir, filename);
//     const destinationPath = path.join(convertedDir, filename);
//     const thumbnailPath = path.join(thumbnailDir, `${path.parse(filename).name}.jpg`); // Thumbnail filename

//     //check if the folders exists otherwise create them
//     fs.mkdir(rawDir, { recursive: true }, (err) => {
//       if (err) {
//         console.error(`Failed to create raw directory ${rawDir}: ${err.message}`);
//         return reject(new Error(`Failed to create raw directory: ${err.message}`));
//       }
//     });
//     fs.mkdir(convertedDir, { recursive: true }, (err) => {
//       if (err) {
//         console.error(`Failed to create converted directory ${convertedDir}: ${err.message}`);
//         return reject(new Error(`Failed to create converted directory: ${err.message}`));
//       }
//     }
//     );
//     fs.mkdir(thumbnailDir, { recursive: true }, (err) => {
//       if (err) {
//         console.error(`Failed to create thumbnail directory ${thumbnailDir}: ${err.message}`);
//         return reject(new Error(`Failed to create thumbnail directory: ${err.message}`));
//       }
//     }
//     );

//     // Calculate the midpoint frame for thumbnail generation
//     // This requires knowing the total number of frames, which FFmpeg can provide
//     // For simplicity, we'll extract a frame at 50% duration instead of frame number

//     // Step 1: Convert the video
//     // Construct the FFmpeg command for video conversion
//     const ffmpegConvertCommand = `ffmpeg -loglevel quiet -nostdin -i "${sourcePath}" -c:v libx264 -c:a aac -pix_fmt yuv420p -profile:v baseline -level 3 "${destinationPath}" -n`;

//     // Execute the FFmpeg command for video conversion
//     exec(ffmpegConvertCommand, (error, stdout, stderr) => {
//       if (error) {
//         console.error(`FFmpeg conversion error for ${filename}: ${stderr}`);
//         return reject(new Error(`FFmpeg conversion failed: ${stderr}`));
//       }
//       console.log(`FFmpeg conversion output for ${filename}: ${stdout}`);

//       // Step 2: Generate the thumbnail
//       // First, get the duration of the video to calculate the midpoint
//       const ffprobeCommand = `ffprobe -v error -select_streams v:0 -show_entries stream=duration -of default=noprint_wrappers=1:nokey=1 "${destinationPath}"`;

//       exec(ffprobeCommand, (probeError, probeStdout, probeStderr) => {
//         if (probeError) {
//           console.error(`FFprobe error for ${filename}: ${probeStderr}`);
//           return reject(new Error(`FFprobe failed: ${probeStderr}`));
//         }

//         const duration = parseFloat(probeStdout);
//         if (isNaN(duration) || duration <= 0) {
//           console.error(`Invalid duration for ${filename}: ${probeStdout}`);
//           return reject(new Error('Invalid video duration.'));
//         }

//         const midpoint = duration / 2;

//         // Construct the FFmpeg command for thumbnail generation
//         const ffmpegThumbnailCommand = `ffmpeg -loglevel quiet -i "${destinationPath}" -ss ${midpoint} -vframes 1 "${thumbnailPath}" -y`;

//         // Ensure the thumbnail directory exists
//         fs.mkdir(thumbnailDir, { recursive: true }, (mkdirErr) => {
//           if (mkdirErr) {
//             return reject(new Error(`Failed to create thumbnail directory ${thumbnailDir}: ${mkdirErr.message}`));
//           }

//           // Execute the FFmpeg command for thumbnail generation
//           exec(ffmpegThumbnailCommand, (thumbError, thumbStdout, thumbStderr) => {
//             if (thumbError) {
//               console.error(`FFmpeg thumbnail error for ${filename}: ${thumbStderr}`);
//               return reject(new Error(`FFmpeg thumbnail generation failed: ${thumbStderr}`));
//             }
//             console.log(`FFmpeg thumbnail output for ${filename}: ${thumbStdout}`);
            
//             // Upload converted video and thumbnail to web server
//             Promise.all([
//               uploadFileToServer(destinationPath),
//               uploadFileToServer(thumbnailPath)
//             ])
//             .then(() => {
//               console.log(`Successfully uploaded files to web server for ${filename}`);
              
//               // Move both the converted video file and thumbnail to Rclone destination
//               moveFileToRclone(destinationPath);
//               moveFileToRclone(thumbnailPath);
              
//               resolve(); // Both conversion, upload, and thumbnail generation succeeded
//             })
//             .catch(error => {
//               console.error(`Error uploading files for ${filename}: ${error.message}`);
//               // Continue with local file handling even if upload fails
//               moveFileToRclone(destinationPath);
//               moveFileToRclone(thumbnailPath);
              
//               resolve(); // Resolve despite upload failure to continue processing queue
//             });
//           });
//         });
//       });
//     });
//   });
// }


//function to move the file to /Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles

//so if filepath is for example: /Users/admin/signCollect/studioFiles/2025-04-25/raw/L20250422_8717.MP4
//then the destination will be /Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles/2025-04-22/raw/L20250422_8717.MP4
//pay attention to folder name, so derive the data from the filename and adjust the folder name. check if the folder exists otherwise create it
//after successfully moving the file, delete the original file
function moveFileToRclone(filePath) {
  // Exit if the file doesn't exist
  if (!fs.existsSync(filePath)) {
    console.error(`Source file does not exist: ${filePath}`);
    return;
  }

  // Extract the filename from the path
  const filename = path.basename(filePath);
  
  // Extract date from filename (e.g., 20250422 from L20250422_8717.MP4)
  const regex = /^[BAMLR](\d{8})_/;
  const dateMatch = filename.match(regex);
  
  if (!dateMatch) {
    console.error(`Invalid filename format for moving: ${filename}`);
    return;
  }
  
  const dateStr = dateMatch[1]; // e.g., '20250422'
  
  // Format date to 'YYYY-MM-DD'
  const formattedDate = `${dateStr.slice(0, 4)}-${dateStr.slice(4, 6)}-${dateStr.slice(6, 8)}`;
  
  // Determine the file type (raw, converted, thumbnails) from the original path
  const pathParts = filePath.split('/');
  const fileType = pathParts[pathParts.length - 2]; // Assumes the file is in a subfolder like "raw"
  
  // Define the destination directory
  const destBaseDir = '/Users/signlab/signCollect/AIHR-FGW-TEST-SIGNLAB (Projectfolder)/studioFiles';
  const destDir = path.join(destBaseDir, formattedDate, fileType);
  
  // Create destination directory if it doesn't exist
  try {
    fs.mkdirSync(destDir, { recursive: true });
  } catch (err) {
    if (err.code !== 'EEXIST') {
      console.error(`Failed to create destination directory ${destDir}: ${err.message}`);
      return;
    }
  }
  
  // Define the destination file path
  const destPath = path.join(destDir, filename);
  
  // Move the file (copy then delete)
  try {
    // Copy the file
    fs.copyFileSync(filePath, destPath);
    console.log(`File copied from ${filePath} to ${destPath}`);
    
    // Check if the destination file exists
    if (fs.existsSync(destPath)) {
      console.log(`Confirmed file exists at destination: ${destPath}`);
    } else {
      console.error(`Warning: File not found at destination: ${destPath}`);
    }
    
    // Delete the original file regardless of whether destination exists
    fs.unlinkSync(filePath);
    console.log(`Original file deleted: ${filePath}`);
  } catch (err) {
    console.error(`Error moving file ${filename}: ${err.message}`);
  }
}

// Function to process the conversion queue
function processQueue() {
  // If already processing, exit
  if (isProcessing) return;

  // Get the next task from the queue
  const nextTask = conversionQueue.shift();

  if (!nextTask) return; // Queue is empty

  isProcessing = true;
  const { cameraId, filename, filePath } = nextTask;

  console.log(`Starting conversion and thumbnail generation for file: ${filename}`);

  // FFmpeg processing commented out
  // convertAndGenerateThumbnail(filePath)
  //   .then(() => {
  //     console.log(`Successfully converted and generated thumbnail for ${filename}`);
  //     // Notify clients about the successful conversion
  //     const conversionData = {
  //       "cameraId": cameraId,
  //       "handle": "conversionCompleted",
  //       "file": filename
  //     };
  //     notifyWebSocketClients(conversionData);
  //     moveFileToRclone(filePath)
  //   })
  //   .catch((error) => {
  //     console.error(`Error processing file ${filename}:`, error);
  //     // Notify clients about the conversion failure
  //     const errorData = {
  //       "cameraId": cameraId,
  //       "handle": "conversionFailed",
  //       "file": filename,
  //       "error": error.message
  //     };
  //     notifyWebSocketClients(errorData);
  //   })
  //   .finally(() => {
      isProcessing = false;
      // Process the next task in the queue
      processQueue();
  //   });
}

// Helper function to send data to WebSocket clients
function notifyWebSocketClients(dataToSend) {
  wss.clients.forEach(function each(client) {
    if (client.readyState === WebSocket.OPEN) {
      client.send(JSON.stringify(dataToSend));
    }
  });
}

// Routes

// GET /latestFile - Returns the latest file in the directory after ensuring it's fully written
server.get('/latestFile', (req, res) => {
  const latestFile = getLatestFile(directoryToServe);
  let isRangeSatisfiable = false;
  let prevRange = 0;

  // Set up an interval to check the file size every 100ms
  const rangeCheckInterval = setInterval(() => {
    const filePath = path.join(directoryToServe, latestFile);
    fs.stat(filePath, (err, stats) => {
      if (err) {
        console.error(`Error accessing file ${latestFile}: ${err.message}`);
        clearInterval(rangeCheckInterval);
        res.status(500).send('Internal Server Error');
        return;
      }

      const fileSize = stats.size;
      console.log(`Previous Size: ${prevRange}, Current Size: ${fileSize}`);

      if (prevRange === fileSize && fileSize > 1) {
        clearInterval(rangeCheckInterval); // Stop the interval
        res.sendFile(filePath);
      } else {
        prevRange = fileSize;
      }
    });
  }, 100);
});

// GET /latestFileName - Returns the name of the latest file
server.get('/latestFileName', (req, res) => {
  const latestFile = getLatestFile(directoryToServe);
  res.send(latestFile);
});

// GET /openVideo - Serves the requested video file
server.get('/openVideo', (req, res) => {
  const query = req.query;
  const videoFileName = query.name; // Get the 'name' query parameter

  if (!videoFileName) {
    res.status(400).send('Video file name not provided');
    return;
  }

  const videoFilePath = path.join(directoryToServe, videoFileName);

  // Check if the requested video file exists
  if (fs.existsSync(videoFilePath)) {
    // Serve the requested video file
    res.sendFile(videoFilePath);
  } else {
    res.status(404).send('Video file not found');
  }
});

// GET /video - Handles video streaming with range requests
server.get("/video", function (req, res) {
  const query = req.query;
  const videoFileName = query.name; // Get the 'name' query parameter

  const range = req.headers.range;
  if (!range) {
    res.status(400).send("Requires Range header");
    return;
  }
  const videoFilePath = path.join(directoryToServe, videoFileName);

  fs.stat(videoFilePath, (err, stats) => {
    if (err) {
      console.error(`Error accessing video file ${videoFileName}: ${err.message}`);
      res.status(404).send('Video file not found');
      return;
    }

    const videoSize = stats.size;
    const CHUNK_SIZE = 10 ** 6; // 1MB
    const start = Number(range.replace(/\D/g, ""));
    const end = Math.min(start + CHUNK_SIZE, videoSize - 1);
    const contentLength = end - start + 1;
    const headers = {
      "Content-Range": `bytes ${start}-${end}/${videoSize}`,
      "Accept-Ranges": "bytes",
      "Content-Length": contentLength,
      "Content-Type": "video/mp4",
    };
    res.writeHead(206, headers);
    const videoStream = fs.createReadStream(videoFilePath, { start, end });
    videoStream.pipe(res);
  });
});

// POST Routes

// POST /completed - Handles 'completed' event
server.post('/completed', (req, res) => {
  const cameraId = req.body.cameraId;
  const fileData = req.body.file;

  // Log received data
  console.log(`Received 'cameraId': ${cameraId}`);
  console.log(`Received 'fileData': ${fileData}`);

  // Respond to the POST request
  res.send('POST request received successfully');

  // Prepare data to send via WebSocket
  const dataToSend = {
    "cameraId": cameraId,
    "handle": "completed",
    "file": fileData
  };

  notifyWebSocketClients(dataToSend);
});

// POST /fileDownloaded - Handles 'fileDownloaded' event
server.post('/fileDownloaded', (req, res) => {
  const cameraId = req.body.cameraId;
  const fileData = req.body.file;

  // Log received data
  console.log(`Received 'cameraId': ${cameraId}`);
  console.log(`Received 'fileData': ${fileData}`);

  // Respond to the POST request
  res.send('POST request received successfully');

  // Prepare data to send via WebSocket
  const dataToSend = {
    "cameraId": cameraId,
    "handle": "fileDownloaded",
    "file": fileData,
    "timestamp": new Date().toISOString()
  };

  notifyWebSocketClients(dataToSend);
});

// Function to scan and process all raw files in date directories
function scanAndProcessRawFiles() {
  console.log("Starting directory scan for raw files...");
  
  // Get all date directories in the base directory
  try {
    const baseDir = directoryToServe;
    const dateDirs = fs.readdirSync(baseDir).filter(item => {
      // Only include directories that match the format YYYY-MM-DD
      const fullPath = path.join(baseDir, item);
      return fs.statSync(fullPath).isDirectory() && /^\d{4}-\d{2}-\d{2}$/.test(item);
    });
    
    console.log(`Found ${dateDirs.length} date directories`);
    
    // Process each date directory
    dateDirs.forEach(dateDir => {
      const rawDir = path.join(baseDir, dateDir, 'raw');
      const convertedDir = path.join(baseDir, dateDir, 'converted');
      const thumbnailDir = path.join(baseDir, dateDir, 'thumbnails');
      
      // Check if raw directory exists
      if (fs.existsSync(rawDir) && fs.statSync(rawDir).isDirectory()) {
        // Get all files in the raw directory
        const rawFiles = fs.readdirSync(rawDir).filter(file => {
          // Only include files that match the camera filename pattern
          return /^[BAMLR]\d{8}_\d{4}\.MP4$/i.test(file);
        });
        
        console.log(`Found ${rawFiles.length} raw files in ${dateDir}/raw`);
        
        // Queue each file for processing if it hasn't been processed already - FFmpeg processing commented out
        // rawFiles.forEach(file => {
        //   const filePath = path.join(rawDir, file);
        //   const convertedFilePath = path.join(convertedDir, file);
        //   const thumbnailFilePath = path.join(thumbnailDir, `${path.parse(file).name}.jpg`);
          
        //   // Check if both the converted file and thumbnail already exist
        //   const isConverted = fs.existsSync(convertedFilePath);
        //   const hasThumbnail = fs.existsSync(thumbnailFilePath);
          
        //   if (isConverted && hasThumbnail) {
        //     console.log(`File ${file} has already been processed (found in converted and thumbnails)`);
        //   } else {
        //     // Add to conversion queue
        //     conversionQueue.push({
        //       cameraId: 'batchProcess', // Using a placeholder ID
        //       filename: file,
        //       filePath: filePath
        //     });
            
        //     console.log(`Enqueued ${file} for batch processing (missing ${!isConverted ? 'converted file' : ''} ${!isConverted && !hasThumbnail ? 'and' : ''} ${!hasThumbnail ? 'thumbnail' : ''})`);
        //   }
        // });
      }
    });
    
    // Start processing the queue if not already processing - FFmpeg processing commented out
    // if (!isProcessing) {
    //   processQueue();
    // }
    
    return { 
      status: 'success', 
      message: `Scanning complete. Queued files for processing.`,
      dateDirectories: dateDirs.length
    };
  } catch (err) {
    console.error(`Error scanning directories: ${err.message}`);
    return { status: 'error', message: err.message };
  }
}

// POST /completedMultiple - Handles 'completedMultiple' event and queues FFmpeg conversion
server.post('/completedMultiple', (req, res) => {
  const cameraId = req.body.cameraId;
  const fileData = req.body.file; // Expected format: B20241024_3211.MP4 or full path

  // Determine if 'fileData' is a full path or just a filename
  let filePath;
  if (path.isAbsolute(fileData)) {
    filePath = fileData;
  } else {
    // Assuming 'fileData' is relative to the raw directory
    // Extract date from filename
    const filename = path.basename(fileData);
    const regex = /^[BAMLR](\d{8})_/;
    const dateMatch = filename.match(regex);
    if (!dateMatch) {
      console.error(`Invalid filename format for enqueuing: ${filename}`);
      res.status(400).send('Invalid filename format.');
      return;
    }
    const dateStr = dateMatch[1]; // e.g., '20241024'
    const formattedDate = `${dateStr.slice(0, 4)}-${dateStr.slice(4, 6)}-${dateStr.slice(6, 8)}`;
    const rawDir = path.join(directoryToServe, `${formattedDate}/raw`);
    filePath = path.join(rawDir, filename);
  }

  // Extract filename from the full path
  const filename = path.basename(filePath);

  // Log received data
  console.log(`Received 'cameraId': ${cameraId}`);
  console.log(`Received 'filePath': ${filePath}`);
  console.log(`Extracted 'filename': ${filename}`);

  // Check if the source file exists
  if (!fs.existsSync(filePath)) {
    console.error(`Source file does not exist: ${filePath}`);
    res.status(404).send('Source file not found.');
    return;
  }

  // Respond immediately to the POST request
  res.send('POST request received successfully');

  // Notify WebSocket clients about the event
  const dataToSend = {
    "cameraId": cameraId,
    "handle": "completedMultiple",
    "file": filename,
    "timestamp": new Date().toISOString()
  };

  notifyWebSocketClients(dataToSend);

  // Scan all directories and process raw files
  const scanResult = scanAndProcessRawFiles();
  console.log(`Directory scan result: ${JSON.stringify(scanResult)}`);
  
  // Enqueue the specific file from the request as well - FFmpeg processing commented out
  // conversionQueue.push({ cameraId, filename, filePath });
  // console.log(`Enqueued file for conversion: ${filename}`);

  // Start processing the queue - FFmpeg processing commented out
  // processQueue();
});

// POST /timeout - Handles 'timeout' event
server.post('/timeout', (req, res) => {
  const cameraId = req.body.cameraId;

  // Log received data
  console.log(`Received 'cameraId': ${cameraId}`);

  // Respond to the POST request
  res.send('POST request received successfully');

  // Prepare data to send via WebSocket
  const dataToSend = {
    "cameraId": cameraId,
    "handle": "timeout",
  };

  notifyWebSocketClients(dataToSend);
});

// POST /device_prop_failed - Handles 'device_prop_failed' event
server.post('/device_prop_failed', (req, res) => {
  const cameraId = req.body.cameraId;

  // Log received data
  console.log(`Received 'cameraId': ${cameraId}`);

  // Respond to the POST request
  res.send('POST request received successfully');

  // Prepare data to send via WebSocket
  const dataToSend = {
    "cameraId": cameraId,
    "handle": "device_prop_failed",
    "status": "failed"
  };

  notifyWebSocketClients(dataToSend);
});

// POST /connected - Handles 'connected' event
server.post('/connected', (req, res) => {
  const cameraId = req.body.cameraId;
  const sdkMode = req.body.sdkMode;
  const cameraNumber = req.body.cameraNumber;

  // Log received data
  console.log(`Received 'cameraId': ${cameraId}`);

  // Respond to the POST request
  res.send('POST request received successfully');

  // Prepare data to send via WebSocket
  const dataToSend = {
    "cameraId": cameraId,
    "handle": "connected",
    "sdkMode": sdkMode,
    "cameraNumber": cameraNumber
  };

  notifyWebSocketClients(dataToSend);
});

// POST /disconnected - Handles 'disconnected' event
server.post('/disconnected', (req, res) => {
  const cameraId = req.body.cameraId;
  const cameraNumber = req.body.cameraNumber;

  // Log received data
  console.log(`Received 'cameraId': ${cameraId}`);

  // Respond to the POST request
  res.send('POST request received successfully');

  const dataToSend = {
    "cameraId": cameraId,
    "handle": "disconnected",
    "cameraNumber": cameraNumber
  };

  notifyWebSocketClients(dataToSend);
});

// POST /reconnecting - Handles 'reconnecting' event
server.post('/reconnecting', (req, res) => {
  const cameraId = req.body.cameraId;

  // Log received data
  console.log(`Received 'cameraId': ${cameraId}`);

  // Prepare data to send via WebSocket
  const dataToSend = {
    "cameraId": cameraId,
    "handle": "reconnecting",
  };

  notifyWebSocketClients(dataToSend);

  // Respond to the POST request
  res.send('POST request received successfully');
});

// POST /recording - Handles 'recording' event
server.post('/recording', (req, res) => {
  const cameraId = req.body.cameraId;
  const state = req.body.state;
  const cameraNumber = req.body.cameraNumber;

  // Log received data
  console.log(`Received 'cameraId': ${cameraId}`);

  // Prepare data to send via WebSocket
  const dataToSend = {
    "cameraId": cameraId,
    "handle": "recording",
    "state": state,
    "cameraNumber": cameraNumber
  };

  notifyWebSocketClients(dataToSend);

  // Respond to the POST request
  res.send('POST request received successfully');
});

// POST /contentsList - Handles 'contentsList' event
server.post('/contentsList', (req, res) => {
  const cameraId = req.body.cameraId;
  const cameraNumber = req.body.cameraNumber;
  const getcontentList = req.body.getcontentList;

  // Log received data
  console.log(`Received 'cameraId': ${cameraId}`);

  // Prepare data to send via WebSocket
  const dataToSend = {
    "cameraId": cameraId,
    "cameraNumber": cameraNumber,
    "handle": "contentlist",
    "getcontentList": getcontentList
  };

  notifyWebSocketClients(dataToSend);

  // Respond to the POST request
  res.send('POST request received successfully');
});

// POST /contentsChanged - Handles 'contentsChanged' event
server.post('/contentsChanged', (req, res) => {
  const cameraId = req.body.cameraId;
  const cameraNumber = req.body.cameraNumber;

  // Log received data
  console.log(`Received 'cameraId': ${cameraId}`);

  // Prepare data to send via WebSocket
  const dataToSend = {
    "cameraId": cameraId,
    "handle": "contentsChanged",
    "cameraNumber": cameraNumber
  };

  notifyWebSocketClients(dataToSend);

  // Respond to the POST request
  res.send('POST request received successfully');
});

// POST /contentsDruk - Handles 'contentsDruk' event
server.post('/contentsDruk', (req, res) => {
  const cameraId = req.body.cameraId;
  const cameraNumber = req.body.cameraNumber;

  // Log received data
  console.log(`Received 'cameraId': ${cameraId}`);

  // Prepare data to send via WebSocket
  const dataToSend = {
    "cameraId": cameraId,
    "cameraNumber": cameraNumber,
    "handle": "contentsDruk",
  };

  notifyWebSocketClients(dataToSend);

  // Respond to the POST request
  res.send('POST request received successfully');
});

// POST /formatCompleted - Handles 'formatCompleted' event
server.post('/formatCompleted', (req, res) => {
  const cameraId = req.body.cameraId;
  const cameraNumber = req.body.cameraNumber;
  const formatMessage = req.body.formatMessage;

  // Log received data
  console.log(`Received 'cameraId': ${cameraId}`);

  // Prepare data to send via WebSocket
  const dataToSend = {
    "cameraId": cameraId,
    "cameraNumber": cameraNumber,
    "handle": "formatCompleted",
    "formatMessage": formatMessage
  };

  notifyWebSocketClients(dataToSend);

  // Respond to the POST request
  res.send('POST request received successfully');
});

// GET /format_media_all - Triggers formatting for all cameras
server.get('/format_media_all', (req, res) => {
  console.log('Received format_media_all request');
  
  // Send success response immediately
  res.send('Format media request received');
  
  // Notify all connected WebSocket clients about formatting start
  const formatStartData = {
    "handle": "formatStarted",
    "message": "Formatting all cameras"
  };
  notifyWebSocketClients(formatStartData);
  
  // In a real implementation, this would trigger the actual format commands
  // to all connected cameras through RemoteCli
  // For now, we'll simulate completion after a delay
  setTimeout(() => {
    // Send format completed notifications for each camera
    const cameras = ['A', 'B', 'L', 'M', 'R'];
    cameras.forEach((camera, index) => {
      setTimeout(() => {
        const formatCompleteData = {
          "cameraId": `Camera ${camera}`,
          "handle": "formatCompleted",
          "formatMessage": `Camera ${camera} format completed successfully`
        };
        notifyWebSocketClients(formatCompleteData);
      }, index * 1000); // Stagger the completion messages
    });
  }, 2000); // Start sending completion messages after 2 seconds
});

// Start the Express server on port 8080
server.listen(8080, () => {
  console.log('Server is listening on port 8080');
  // convertAndGenerateThumbnail('/Users/admin/signCollect/studioFiles/2025-04-25/raw/L20250422_8717.MP4') - FFmpeg processing commented out
});

// WebSocket Server Event Handling
wss.on('connection', (socketwss) => {
  console.log('WebSocket Client connected');

  // Handle incoming messages from clients
  socketwss.on('message', (message) => {
    console.log(`Received message from client: ${message}`);
    // You can handle incoming messages here if needed
  });

  // Handle client disconnection
  socketwss.on('close', () => {
    console.log('WebSocket Client disconnected');
  });
});
const UPLOAD_URL = 'https://signcollect.nl/videoProc/upload2.php';

/**
 * Upload a single file to signcollect.nl
 * @param  {string} filePath  Absolute path to your file
 * @return {Promise<Object>}  Resolves with the parsed JSON from the server
 */
async function uploadFileToServer(filePath) {
  if (!fs.existsSync(filePath)) {
    throw new Error(`File not found: ${filePath}`);
  }

  // determine upload field
  const ext = path.extname(filePath).toLowerCase();
  let field;
  if (['.jpg', '.jpeg', '.png'].includes(ext)) {
    field = 'thumbnail';
  } else if (['.mp4', '.mov', '.MP4'].includes(ext)) {
    field = 'video';
  } else {
    throw new Error(`Unsupported extension: ${ext}`);
  }

  // build multipart form
  const form = new FormData();
  form.append(field, fs.createReadStream(filePath), path.basename(filePath));
  console.log(`→ uploading ${filePath} as “${field}”`);

  // merge headers
  const headers = {
    ...form.getHeaders(),
    'Accept': 'application/json'
  };

  try {
    const resp = await axios.post(UPLOAD_URL, form, {
      headers,
      timeout: 3 * 60_000
    });

    console.log(`← ${field} upload succeeded. Server replied:`, resp.data);
    return resp.data;
  }
  catch (err) {
    // server returned a non-2xx
    if (err.response) {
      console.error(`← upload failed with status ${err.response.status}:`,
        JSON.stringify(err.response.data, null, 2));
    } else {
      console.error(`← upload error:`, err.message);
    }
    throw err;
  }
}

module.exports = uploadFileToServer;






