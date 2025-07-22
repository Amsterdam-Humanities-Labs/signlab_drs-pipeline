import requests
import json
import os

class VideoAPIClient:
    """
    A client library for interacting with the Video Render Management API.
    """
    
    def __init__(self, base_url):
        """
        Initialize the API client.
        
        Args:
            base_url (str): The base URL of the API without trailing slash
        """
        self.base_url = base_url.rstrip('/')
    
    def _get_request(self, action, params=None):
        """
        Make a GET request to the API.
        
        Args:
            action (str): The API action to call
            params (dict, optional): Additional URL parameters
            
        Returns:
            dict or list: The JSON response from the API
        """
        url = f"{self.base_url}/api.php"
        
        # Initialize params dict if not provided
        if params is None:
            params = {}
        
        # Add action parameter
        params['action'] = action
        
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()  # Raise exception for 4XX/5XX responses
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error making GET request: {e}")
            return {"error": str(e)}
    
    def _post_request(self, action, data=None):
        """
        Make a POST request to the API.
        
        Args:
            action (str): The API action to call
            data (dict, optional): The POST data to send
            
        Returns:
            dict: The JSON response from the API
        """
        url = f"{self.base_url}/api.php"
        params = {'action': action}
        
        try:
            response = requests.post(url, params=params, data=data)
            response.raise_for_status()  # Raise exception for 4XX/5XX responses
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error making POST request: {e}")
            return {"error": str(e)}
    
    # --- GET Methods ---
    
    def get_videos(self, date=None):
        """
        Get videos, optionally filtered by date.
        
        Args:
            date (str, optional): Filter videos by this date
            
        Returns:
            list: List of video records
        """
        params = {}
        if date:
            params['date'] = date
        return self._get_request('get_videos', params)
    
    def get_dates(self):
        """
        Get all unique dates in the database.
        
        Returns:
            list: List of dates
        """
        return self._get_request('get_dates')
    
    def get_videos_rendered_null(self):
        """
        Get videos that have not been rendered yet.
        
        Returns:
            list: List of unrendered video records
        """
        return self._get_request('get_videos_rendered_null')
    
    def get_videos_converted_null(self):
        """
        Get videos that have not been converted yet.
        
        Returns:
            list: List of unconverted video records
        """
        return self._get_request('get_videos_converted_null')
    
    def get_videos_thumbnail_null(self):
        """
        Get videos that don't have thumbnails yet.
        
        Returns:
            list: List of video records without thumbnails
        """
        return self._get_request('get_videos_thumbnail_null')
    
    def get_tyd_rendered_null(self):
        """
        Get videos that have not been TYD rendered yet.
        
        Returns:
            list: List of video records without TYD rendering
        """
        return self._get_request('get_tyd_rendered_null')

    def get_tyd_rendered_noncropped(self):
        """
        Get TYD videos that have been rendered but not yet cropped.
        
        Returns:
            list: List of TYD video records with rendered=1
        """
        return self._get_request('get_tyd_rendered_noncropped')
    
    def get_tyd_converted_null(self):
        """
        Get videos that have not been TYD converted yet.
        
        Returns:
            list: List of video records without TYD conversion
        """
        return self._get_request('get_tyd_converted_null')
    
    def get_tyd_thumbnail_null(self):
        """
        Get videos that don't have TYD thumbnails yet.
        
        Returns:
            list: List of video records without TYD thumbnails
        """
        return self._get_request('get_tyd_thumbnail_null')
    
    def get_videos_rendered_noncropped(self):
        """
        Get videos that have been rendered but not yet cropped.
        
        Returns:
            list: List of video records with rendered=1
        """
        return self._get_request('get_videos_rendered_noncropped')
    
    # --- UPDATE Methods ---
    
    def update_rendered(self, file, file_type):
        """
        Mark a video as rendered.
        
        Args:
            file (str): The filename
            file_type (str): The file field type ('l_file', 'm_file', or 'r_file')
            
        Returns:
            dict: API response
        """
        data = {
            'file': file,
            'fileType': file_type
        }
        return self._post_request('update_rendered', data)
    
    def update_converted(self, file, file_type):
        """
        Mark a video as converted.
        
        Args:
            file (str): The filename
            file_type (str): The file field type ('l_file', 'm_file', or 'r_file')
            
        Returns:
            dict: API response
        """
        data = {
            'file': file,
            'fileType': file_type
        }
        return self._post_request('update_converted', data)
    
    def update_thumbnail(self, file):
        """
        Mark a video as having a thumbnail.
        
        Args:
            file (str): The middle filename
            
        Returns:
            dict: API response
        """
        data = {
            'file': file,
            'fileType': 'm_file'  # Thumbnail is always based on m_file
        }
        return self._post_request('update_thumbnail', data)
    
    def update_tyd_rendered(self, file, file_type):
        """
        Mark a video as TYD rendered.
        
        Args:
            file (str): The filename
            file_type (str): The file field type ('l_file', 'm_file', or 'r_file')
            
        Returns:
            dict: API response
        """
        data = {
            'file': file,
            'fileType': file_type
        }
        return self._post_request('update_tyd_rendered', data)
    
    def update_tyd_converted(self, file, file_type):
        """
        Mark a video as TYD converted.
        
        Args:
            file (str): The filename
            file_type (str): The file field type ('l_file', 'm_file', or 'r_file')
            
        Returns:
            dict: API response
        """
        data = {
            'file': file,
            'fileType': file_type
        }
        return self._post_request('update_tyd_converted', data)
    
    def update_tyd_thumbnail(self, file, file_type):
        """
        Mark a video as having a TYD thumbnail.
        
        Args:
            file (str): The filename
            file_type (str): The file field type ('l_file', 'm_file', or 'r_file')
            
        Returns:
            dict: API response
        """
        data = {
            'file': file,
            'fileType': file_type
        }
        return self._post_request('update_tyd_thumbnail', data)
    
    def update_rendered_cropped(self, file, file_type):
        """
        Mark a video as having completed the cropping process (rendered=2).
        
        Args:
            file (str): The filename
            file_type (str): The file field type ('l_file', 'm_file', or 'r_file')
            
        Returns:
            dict: API response
        """
        data = {
            'file': file,
            'fileType': file_type
        }
        return self._post_request('update_rendered_cropped', data)
    
    def update_tyd_rendered_cropped(self, file, file_type):
        """
        Mark a video as having completed the TYD cropping process (tyd_rendered=2).
        
        Args:
            file (str): The filename
            file_type (str): The file field type ('l_file', 'm_file', or 'r_file')
            
        Returns:
            dict: API response
        """
        data = {
            'file': file,
            'fileType': file_type
        }
        return self._post_request('update_tyd_rendered_cropped', data)
    
    # --- RESET Method ---
    
    def reset_status(self, date, status_type):
        """
        Reset statuses for videos from a specific date.
        
        Args:
            date (str): The date to filter videos (YYYY-MM-DD)
            status_type (str): Type of status to reset ('rendered', 'converted', 'thumbnail', 
                             'tyd_rendered', 'tyd_converted', 'tyd_thumbnail', or 'all')
            
        Returns:
            dict: API response
        """
        data = {
            'date': date,
            'type': status_type
        }
        return self._post_request('reset_status', data)


# Example script using the client
if __name__ == '__main__':
    # URL of your API
    API_URL = 'https://signcollect.nl/renderServer'
    
    # Create client instance
    client = VideoAPIClient(API_URL)
    
    # Example 1: Get videos that need rendering
    print("Getting videos that need rendering...")
    unrendered = client.get_videos_rendered_null()
    print(f"Found {len(unrendered)} unrendered videos")
    
    # Example 2: Process videos that need rendering
    if unrendered:
        print(f"Processing first unrendered video: {unrendered[0]['m_file']}")
        # Here you would do actual rendering work
        # ...
        
        # Then mark as rendered
        result = client.update_converted(unrendered[0]['m_file'], 'm_file')
        print(f"Update result: {result}")
    
    # Example 3: Get all dates
    dates = client.get_dates()
    print(f"Available dates: {dates}")
    
    # Example: Get videos that have been rendered but need cropping
    print("Getting videos that need cropping...")
    non_cropped = client.get_videos_rendered_noncropped()
    print(f"Found {len(non_cropped)} videos that need cropping")
    
    # Example: Process videos that need cropping
    if non_cropped:
        print(f"Processing first non-cropped video: {non_cropped[0]['m_file']}")
        # Here you would do actual cropping work
        # ...
        
        # Then mark as cropped
        result = client.update_rendered_cropped(non_cropped[0]['m_file'], 'm_file')
        print(f"Update result: {result}")
    
    # Example: Get TYD videos that need cropping
    print("Getting TYD videos that need cropping...")
    tyd_non_cropped = client.get_tyd_rendered_noncropped()
    print(f"Found {len(tyd_non_cropped)} TYD videos that need cropping")
    
    # Example: Process TYD videos that need cropping
    if tyd_non_cropped:
        print(f"Processing first TYD non-cropped video: {tyd_non_cropped[0]['m_file']}")
        # Here you would do actual TYD cropping work
        # ...
        
        # Then mark as TYD cropped
        result = client.update_tyd_rendered_cropped(tyd_non_cropped[0]['m_file'], 'm_file')
        print(f"Update result: {result}")
    
    # Uncomment to test reset functionality
    # if dates:
    #     latest_date = dates[0]
    #     print(f"Resetting all statuses for date: {latest_date}")
    #     result = client.reset_status(latest_date, 'all')
    #     print(f"Reset result: {result}")
