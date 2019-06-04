from apiclient.http import MediaIoBaseUpload
from bs4 import BeautifulSoup
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from retrying import retry

import io
import logging
import os
import pickle
import requests
import time

# If True, only save videos from Panopto folders that contain 'CSE' in their name
ONLY_SAVE_CSE_VIDEOS = True
# The name of the Google Drive folder to save the videos under.
# Note: the folder will be created if it doesn't already exist.
GOOGLE_DRIVE_FOLDER_NAME = 'Panopto Videos'

# Store auth credentials as environment variables.
USERNAME = os.environ.get('PANOPTO_USERNAME')
PASSWORD = os.environ.get('PANOPTO_PASSWORD')

# The base URL for accessing a Panopto folder.
FOLDER_URL_BASE = 'https://uw.hosted.panopto.com/Panopto/Pages/Sessions/List.aspx#folderID=%22{}%22'
# The Panopto Sessions URL. POSTing to this address will return a list
# of videos in a folder.
SESSIONS_URL = 'https://uw.hosted.panopto.com/Panopto/Services/Data.svc/GetSessions'
# The parameters to include when POSTing to the above address in order to get
# the list of videos from a folder. Two things are configurable here:
#  - maxResults -- the number of videos to return
#  - folderID   -- the ID of the folder to search through
SESSIONS_PARAMETERS = '{{"queryParameters":{{"query":null,"sortColumn":1,"sortAscending":true,"maxResults":{},"page":0,"startDate":null,"endDate":null,"folderID":{},"bookmarked":false,"getFolderData":true,"isSharedWithMe":false,"includePlaylists":true}}}}'
# The scopes that the Google Drive service should have access to
# In this case, the program needs full read/write access of Google Drive.
# I'm not using it for anything bad, I promise.
SCOPES = ['https://www.googleapis.com/auth/drive']

logging.getLogger().setLevel(logging.INFO)


def get_folder_videos(folder_id, video_limit, client):
    """Get all the videos inside a given folder.

    Parameters:
        folder_id (str): the ID of the folder to search through
        video_limit (int): the maximum number of videos to get
        client (requests.Session): the authenticated requests session to use

    Returns:
        None if the request failed (this seems to only happen for courses
            I've TA'd, not for any I've actually taken)
        A list of up to video_limit (URL, Name) tuples otherwise
    """
    headers = {'Content-type': 'application/json'}
    parameters = SESSIONS_PARAMETERS.format(video_limit, folder_id)
    response = client.post(SESSIONS_URL, data=parameters, headers=headers)

    if response.status_code != 200:
        logging.warning('Could not get videos for folder ID: {}'
                        .format(folder_id))
        return None

    results = response.json()['d']['Results']

    video_info = [(video['IosVideoUrl'].replace('.hls/master.m3u8', '.mp4'),
                   video['SessionName']) for video in results]
    return video_info


def negotiate_saml():
    """Negotiate the Canvas SAML authentication process.

    Raises:
        TypeError if PANOPTO_USERNAME/PANOPTO_PASSWORD environment variables
            are not set, or are invalid.
        AssertionError if some SAML requests fail.

    Returns:
        A SAML-authenticated requests session
    """
    # https://stackoverflow.com/a/16646366 was super helpful for figuring out
    # the general flow of the SAML process. The specifics were figured out
    # using Firefox's network inspector.
    logging.info('Beginning SAML negotiation...')
    client = requests.session()

    logging.info('Beginning Canvas SAML')
    saml_request = client.get('https://canvas.uw.edu/login/saml/83')
    cookie_dict = client.cookies.get_dict()
    assert '_csrf_token' in cookie_dict, 'Missing cookies.'
    assert 'bbbbbbbbbbbbbbb' in cookie_dict, 'Missing cookies.' 
    assert 'JSESSIONID' in cookie_dict, 'Missing cookies.'

    payload = {
            'j_username': USERNAME,
            'j_password': PASSWORD,
            '_eventId_proceed': 'Sign+in'
            }

    logging.info('Requesting sign in payload...')
    saml_response = client.post(saml_request.url, data=payload)

    soup = BeautifulSoup(saml_response.text, 'html.parser')
    # The SAML Response, which needs to be POSTed, is contained in a hidden input
    # field which is normally auto-submitted when the page loads.
    try:
        saml_response_value = soup.find('input', {'name': 'SAMLResponse'})['value']
    except TypeError:
        # r4 will have status code 200 even if invalid credentials are supplied
        # which means we will only know if the credentials are invalid if the
        # BeautifulSoup parsing fails
        logging.error('Type error -- are PANOPTO_USERNAME and '
                      'PANOPTO_PASSWORD environment variables set?')
        raise

    payload = {
            'SAMLResponse': saml_response_value
            }

    logging.info('POSTing SAML response...')
    saml_end = client.post('https://canvas.uw.edu/login/saml', data=payload)
    logging.info('Connecting to Panopto...')
    panopto_request = client.get('https://uw.hosted.panopto.com/Panopto/Pages/Auth/Login.aspx?instance=UWNetid&ReturnUrl=https%3a%2f%2fuw.hosted.panopto.com%2fPanopto%2fPages%2fHome.aspx')
    cookie_dict = client.cookies.get_dict()
    assert '.ASPXAUTH' in cookie_dict, 'Missing cookie.'
    return client


def google_drive_auth():
    """Get the Google Drive authentication credentials.

    If the credentials are stored in token.pickle, load those.
    If the credentials have expired, refresh them if possible.
    Otherwise, open the browser authorization page. Note that this step would
        require credentials.json which contains the program's API credentials.

    If new credentials are created, pickle them and store as token.pickle

    Returns:
        Google drive credentials
    """
    logging.info('Authenticating with Google Drive...')
    # Adapted from: https://developers.google.com/drive/api/v3/quickstart/python
    creds = None
    # The file token.pickle stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first
    # time.
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        logging.info('Invalid creds')
        if creds and creds.expired and creds.refresh_token:
            logging.info('Refreshing creds...')
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            creds = flow.run_local_server()
        # Save the credentials
        with open('token.pickle', 'wb') as token:
            logging.info('Saving creds to file')
            pickle.dump(creds, token)

    return creds


@retry(wait_exponential_multiplier=1000, wait_exponential_max=10000)
def create_folder_if_not_exists(folder_name, drive_service, parent_folder=None):
    """Create a Google Drive folder if it doesn't already exist.

    If the request fails, it will retry with exponential backoff until the
    request succeeds, or until the delay increases beyond 10s.

    Parameters:
        folder_name (str): The name of the folder
        drive_service (Google Drive API service): The Drive service
        parent_folder (str): The name of the parent folder to create the folder
            in. Default: None (i.e. no parent folder)

    Returns:
        The Google Drive ID of the folder
    """
    folder_id = None
    logging.info('Checking if folder: {} exists'.format(folder_name))
    response = drive_service.files().list(q='mimeType="application/vnd.google-apps.folder" and name="{}"'
                                          .format(folder_name),
                                          fields='files(id)').execute()

    if not response['files']:
        logging.info('Creating folder: {}'.format(folder_name))
        file_metadata = {
                    'name': folder_name,
                    'mimeType': 'application/vnd.google-apps.folder'
                }

        if parent_folder is not None:
            file_metadata['parents'] = [parent_folder]

        response = drive_service.files().create(body=file_metadata,
                                                fields='id').execute()
        folder_id = response.get('id')
        logging.info('Created {}; Folder ID: {}'
                     .format(folder['Name'], folder_id))
    else:
        folder_id = response['files'][0]['id']

    return folder_id


@retry(wait_exponential_multiplier=1000, wait_exponential_max=10000)
def check_if_file_exists(file_name, drive_service, parent_folder=None):
    """Check if a file exists on Google Drive.


    If the request fails, it will retry with exponential backoff until the
    request succeeds, or until the delay increases beyond 10s.

    Parameters:
        file_name (str): The name of the file
        drive_service (Google Drive API service): The Drive service
        parent_folder (str): The name of the parent folder to create the folder
            in. Default: None (i.e. no parent folder)

    Returns:
        True if the file exists,
        False otherwise
    """
    response = drive_service.files().list(q='"{}" in parents and name="{}.mp4"'
                                          .format(parent_folder, file_name),
                                          fields='files(id)').execute()

    if response['files']:
        return True
    return False


if __name__ == '__main__':
    authed_client = negotiate_saml()

    drive_creds = google_drive_auth()
    drive_service = build('drive', 'v3', credentials=drive_creds)

    panopto_folder_id = create_folder_if_not_exists(GOOGLE_DRIVE_FOLDER_NAME,
                                                    drive_service)

    logging.info('Requesting folders information...')
    panopto_folders_response = authed_client.get('https://uw.hosted.panopto.com/Panopto/Api/Folders?parentId=null&folderSet=1&includeMyFolder=false&includePersonalFolders=true&page=0&sort=Depth&names[0]=SessionCount')
    folders = panopto_folders_response.json()
    for folder in folders:
        if folder['SessionCount'] > 0 and (not ONLY_SAVE_CSE_VIDEOS or 'CSE' in folder['Name']):
            logging.info('Getting video URLs...')
            videos = get_folder_videos(folder['Id'], 50, authed_client)

            if videos is None:
                continue

            folder_id = create_folder_if_not_exists(folder['Name'],
                                                    drive_service,
                                                    panopto_folder_id)

            for video_url, video_name in videos:
                file_exists = check_if_file_exists(video_name,
                                                   drive_service,
                                                   folder_id)

                if file_exists:
                    logging.debug('{} already exists; skipping over it'
                                  .format(video_name))
                else:
                    logging.debug('Video URL: {}'.format(video_url))
                    logging.debug('Video Name: {}'.format(video_name))

                    logging.info('Uploading video without streaming...')
                    start = time.time()

                    g = requests.get(video_url)
                    g_bytes = io.BytesIO(g.content)
                    logging.info('Video download complete...')

                    download_time = time.time() - start
                    logging.debug('Time to download: {}'.format(download_time))

                    media = MediaIoBaseUpload(g_bytes, mimetype='video/mp4')
                    body = {
                           'name': video_name + '.mp4',
                           'parents': [folder_id]
                           }
                    drive_service.files().create(body=body, media_body=media,
                                                 fields='id').execute()

                    end = time.time()
                    logging.info('Finished uploading non-streamed video.')
                    logging.debug('Time to upload: {}'
                                  .format(end - start - download_time))
                    logging.debug('Total time taken: {}'.format(end - start))
