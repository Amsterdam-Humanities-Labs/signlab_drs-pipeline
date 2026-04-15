The API is live and fully functional. Here's the summary:                     
                                                                                
  Deployed at: https://signcollect.nl/drs_ep/api.php                            
                 
  What was done:                                                                
  1. Copied drs_ep_api.php to api.php (matching the guide's expected filename)  
  2. Changed the directory group to www-data so Apache can write the JSON data
  file
  3. Tested claim, get_active, and release — all working
  4. render_claims.json and .lock file auto-created successfully

  Your DaVinci Resolve machines can now use:

  https://signcollect.nl/drs_ep/api.php?action=claim
  https://signcollect.nl/drs_ep/api.php?action=claim_batch
  https://signcollect.nl/drs_ep/api.php?action=complete
  ...etc
