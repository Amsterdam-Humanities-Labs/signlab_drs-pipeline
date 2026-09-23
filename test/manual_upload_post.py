import os
import requests

def upload_video(video_path):
    """Upload the video to the processing server"""
    upload_url = "https://signcollect.nl/videoProc/upload_post.php"
    
    if not os.path.exists(video_path):
        print(f"Error: Video file not found at {video_path}")
        return False
    
    try:
        print(f"Uploading video: {video_path}")
        filename = os.path.basename(video_path)
        
        # Create form data with the video file
        with open(video_path, 'rb') as f:
            files = {'video': (filename, f, 'video/mp4')}
            
            # Send the POST request with SSL verification disabled
            response = requests.post(upload_url, files=files)

        print(f"HTTP Status Code: {response.status_code}")
        # Strip whitespace from response text for cleaner checking
        response_text = response.text.strip()
        print(f"Response text: {response_text}")
        
        if response.status_code == 200:
            if "Video uploaded successfully." in response_text:
                print("Upload successful.")
                return True
            else:
                print(f"Upload reported as failed by server: {response_text}")
                return False
        else:
            print(f"Upload failed with HTTP status code {response.status_code}: {response_text}")
            return False
    
    except Exception as e:
        print(f"Upload error: {str(e)}")
        return False
    

upload_video("M20240321_0004.mp4")  # Replace with the path to your video file