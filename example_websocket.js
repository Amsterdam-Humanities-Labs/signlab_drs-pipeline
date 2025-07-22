var cameraArrayList = [];
  socket.addEventListener('message', (event) => {
    if (startupFirstTime == 0) {
      const rC = JSON.parse(event.data);

      if (event.data) {
        const cId = rC.cameraId;
        if (!cA.hasOwnProperty(cId)) {
          cA[cId] = {};
          cA[cId]["aantal_opnames"] = 0;
        }

        if (rC.handle == "fileDownloaded") {
          console.log(rC.file);
          // cA[cId]["downloadCount"]++;
          downloadCount++;
          // $("#downloadContainer").append(rC.file);
          // $("#downloadContainer").append("<br>");
          //we want to display progress bar on the downloadContainer by using downloadCount and opnameCounter
          opnameCounterA = opnameCounter * 5 //aantal cameras
          progressBar = (downloadCount / opnameCounterA) * 100;
          console.log(progressBar, downloadCount, opnameCounterA);
          //we then create progress bar in downloadContainer
          $("#downloadContainer").html('<div class="progress"><div class="progress-bar" role="progressbar" style="width: ' + progressBar + '%" aria-valuenow="' + progressBar + '" aria-valuemin="0" aria-valuemax="100"></div></div>'); 
          // Extract the basename from the filepath
          const filepath = rC.file;
          const basename = filepath.split('/').pop();

          // Check if the filename starts with B, A, L, M, or R
          if (/^[BALMR]/i.test(basename)) {
            const firstChar = basename.charAt(0).toUpperCase();
            const tableBody = $('#fileTable tbody');

            // Find if a row with the same starting letter already exists
            const existingRow = tableBody.find(`tr[data-letter="${firstChar}"]`);

            if (existingRow.length > 0) {
              // Replace the existing row's filename
              existingRow.find('.filename').text(basename);
            } else {
              // If the table has 5 rows, remove the oldest one
              if (tableBody.find('tr').length >= 5) {
                tableBody.find('tr').first().remove();
              }
              // Append the new row
              tableBody.append(`
                <tr data-letter="${firstChar}">
                  <td>${firstChar}</td>
                  <td class="filename">${basename}</td>
                </tr>
              `);
            }
          }

          // watchDogDownload(1); //we keep resetting the timer to 5 minutes, but if after 5 minutes nothing happens then the cameras will disconnected and reconnected for redownload.
          //this happens sometimes when the cameras are not responding or my code is just faulty.

          if (cA[cId]["downloadCount"] == opnameCounter) {
            $("#downloadContainer").append(cA[cId]["cameraId"] + " Klaar");
            $("#downloadContainer").append("<br>");

            $("#downloadContainer").html("");
            //we reset the timeNow to 0
            downloadCount = 0;
          }
        }

        if (rC.handle == "recording") {
          cA[cId]["cameraNumber"] = rC.cameraNumber;
          cA[cId]["state"] = rC.state;

          if (rC.state == "recording") {
            startRecording(cA);
            fetch('fetch_last_capture.php?status=opnameCounterAdd&cameraId=' + cId, {
              method: 'GET',
            })
              .then(response => response.json())
              .then(data => {
                cA[cId]["aantal_opnames"] = data["opnameCounter"];
              })
              .catch(error => {
                console.error('Error:', error);
              });
              opnameCounterAdd();
              sendStartorStopCapture(tekstId, "text_start", "start");

    

          } else if (rC.state == "stopping") {
            stopRecording(rC.cameraNumber, cA, cId);
            sendStartorStopCapture(tekstId, "text_start", "stop");

          }
        }
        if (rC.handle == "reconnecting") {
          cA[cId]["connected"] = "reconnecting";
          changeStatus(cA);
        }
        if (rC.handle == "connected") {
          cA[cId]["connected"] = "connected";
          cA[cId]["sdkMode"] = rC.sdkMode;
          cA[cId]["cameraNumber"] = rC.cameraNumber;
          //add rc.cameraNumber to array but only once
          if (!cameraArrayList.includes(rC.cameraNumber)) {
            cameraArrayList.push(rC.cameraNumber);
          }

          changeStatus(cA);
          opnameCounterAdd();
        }
        if (rC.handle == "timeout") {
          cA[cId]["connected"] = "timeout";
          changeStatus(cA);
        }
        if (rC.handle == "disconnected") {
          if (statusDownload() == "multiple") {
            cA[cId]["switchtoSDK"] = 1;
          }
          cA[cId]["connected"] = "disconnected";
          cA[cId]["cameraNumber"] = rC.cameraNumber;

          if (cA[cId]["switchtoSDK"] == 1) {
            setTimeout(function () {
              fetchAndProcess('http://localhost:18080/connect_camera_contents_ind', rC.cameraNumber, "json");
            }, 1000);
            cA[cId]["switchtoSDK"] = 0;
          } else {
            setTimeout(function () {
              fetchAndProcess('http://localhost:18080/connect_camera_remote_ind', rC.cameraNumber, "json");
            }, 1000);
          }
        }
        if (rC.handle == "device_prop_failed") {
          cA[cId]["connected"] = "device_prop_failed";
          changeStatus(cA);
        }
        if (rC.handle == "completed") {
          cA[cId]["file"] = rC.file;
          cA[cId]["download"] = "completed";
          startDisplayingVideos(cId);
        }
        if (rC.handle == "completedMultiple") {
          downloadedFinished++;
          console.log("downloadedFinished", downloadedFinished, "totalCameras", totalCameras);
          if (downloadedFinished == totalCameras) {
            $("#bezigDownload").modal('hide');
            downloadedFinished = 0;
            statusDownload("single");
            //fetchAndProcess('http://localhost:18080/camera_disconnect', rC.cameraNumber, "json");

            fetch('fetch_last_capture.php?processed=0', {
              method: 'GET',
            });

            //if cameras memory is nearly empty, then after uploading all media we are going to format the cameras
            // if (uploadAndFormat == 1) {
            //   fetchAndProcess('http://localhost:18080/format_media_all', "json");
            // }x
            // watchDogDownload(0);

           
          }
        }
        if (rC.handle == "contentsChanged") {
          if (statusDownload() == "single") {
            fetchAndProcess('http://localhost:18080/ophalen_movie', rC.cameraNumber, "json");
          } else {
            console.log(rC.cameraNumber);
            fetchAndProcess('http://localhost:18080/ophalen_movie_multiple', rC.cameraNumber, "json");
            $("#downloadConfirmModal").modal('hide');
            $("#bezigDownload").modal('show');
            $("#downloadContainerText").append("<br> Verbonden met Camera " + rC.cameraNumber);
            //we are going to set timenow to now
            // watchDogDownload(1);
          }
        }
        if (rC.handle == "contentsDruk") {
          fetchAndProcess('http://localhost:18080/camera_disconnect_ind', rC.cameraNumber, "json");
          cA[cId]["switchtoSDK"] = 1;
        }
        if (rC.handle == "formatCompleted") {
          cA[cId]["aantal_opnames"] = 0;

          let klaarFormat = 0;
          Object.keys(cA).forEach((item) => {
            if (cA[item]["aantal_opnames"] == 0) {
              klaarFormat++;
            }
          });

          if (klaarFormat == totalCameras) {
            $("#myModal3Div").append("Camera " + rC.cameraNumber + " Formatten klaar");
            setTimeout(function () {
              myModal3.hide();
            }, 3000);
            startupFormat = 0;

            fetch('fetch_last_capture.php?status=opnameCounter', {
              method: 'GET',
            })
              .then(response => response.json())
              .then(data => {
                if(data.opnameCounter == 0)
              {
                console.log("opnameCounter is 0", opnameCounter);
                opnameCounter = 0
                startCalibration();
              }
              })
              uploadAndFormat = 0;
          }
        }
      }t
    }
  });

  // camera disconnect
    fetchAndProcess('http://localhost:18080/camera_disconnect', "json");


    //trigger download videos
                fetchAndProcess('http://localhost:18080/ophalen_movie_multiple', rC.cameraNumber, "json");



                //camera reconnect and check status: 


  async function camerasReconnectStartup() {
    const numIterations = 10;
    camerasReconnectStartupRunning = 1;

    for (let i = 0; i < numIterations; i++) {
      let cameraSuccess = 0;
      totalCameras = 0;
      const checkStatus = await fetchAndProcess('http://localhost:18080/check_status', "", "json");
      console.log(checkStatus)
      if (checkStatus) {
        for (const item of checkStatus) {
          totalCameras = totalCameras + 1;
        }
        for (const item of checkStatus) {
          if (item.status == true && item.state == 0) {
            cameraSuccess = cameraSuccess + 1;
            $("#cameraText").text("Cameras online: " + cameraSuccess);
          } else if (item.status == "fail_please_use_disconnect") {
            const disconnect_camera = await fetchAndProcess("http://localhost:18080/connect_camera_remote_ind", item.camera_id, "json");
            await new Promise(resolve => setTimeout(resolve, 1000));

            if (disconnect_camera[0].status == "disconnect_failed_try_connect") {
              await fetchAndProcess("http://localhost:18080/connect_camera_remote_ind", item.camera_id, "json");
              await new Promise(resolve => setTimeout(resolve, 1000));
            }
          } else if (!item.status) {
            await fetchAndProcess("http://localhost:18080/connect_camera_remote_ind", item.camera_id, "json");
            await new Promise(resolve => setTimeout(resolve, 1000));
          } else if (item.state == 1 && item.status) {
            await fetchAndProcess("http://localhost:18080/camera_disconnect_ind", item.camera_id, "json");
            await new Promise(resolve => setTimeout(resolve, 1000));
          }

          if (cameraSuccess == totalCameras) {
            foundSuccess = true;
          }
        }

        if (foundSuccess) {
          $("#cameraText").text("Alle Cameras online: " + cameraSuccess);
          startupFirstTime = 0;

          if (formatOnce == 1) {
            fetch('fetch_last_capture.php?format=1', {
              method: 'GET',
            }).then(response => response.json())
              .then(data => {
                if (data.result) {
                  if (data.result == 0) {
                    startupFormat = 0;
                  } else {
                    setTimeout(function () {
                    formatModal();

                  }, 1000);
                    // myModal3.show();
                    // formatModal.show();
                    formatOnce = 0;
                    $("#bodyText").text("Klaar...");
                  }
                }
              }).catch(function (error) {
                console.error('Error:', error);
              });
          }

         

          interval = setInterval(() => {
            startCheckSettings();
          }, 1000);

          break;
        }
      }
    }
  }


  async function fetchAndProcess(url, n, form) {
  if(debug == 1)
  {
    return 1;
  }
    fetch_timenow = new Date().getTime();
    try {
      const finalUrl = url + "?n=" + n;
      const response = await fetchWithTimeout(finalUrl, { timeout: 6000000 });
      if (form == "json") {
        const data = await response.json();
        return data;
      } else {
        const data = await response.text();
        return data;
      }
    } catch (error) {
      console.error('Fetch error:', error, url);
      return 0;
    }
  }