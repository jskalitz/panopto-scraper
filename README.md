# Upload All Videos From Panopto to Google Drive

I'm graduating in a few days, I love Panopto, and I also have unlimited storage on Google Drive. Archiving all the Panopto videos I have access to seems only natural. This is a Python script to do that.

## Requirements
Python 3.6, pip, and a good internet connection.

## Installation
Clone the project. Create a Python virtual environment. Run

```bash
pip install -r requirements.txt
```
to install the required pip packages.

## Usage
First, you'll need Google Drive API credentials. Go [here](https://developers.google.com/drive/api/v3/quickstart/python) to get those, and save them as credentials.json in the project directory.

You'll also need to store your username and password as environment variables, like so:
```bash
export PANOPTO_USERNAME='username'
export PANOPTO_PASSWORD='password'
```

 Then run

```bash
python3 panopto.py
```

By default, this will save the videos in subfolders under the folder 'Panopto Videos' in your Google Drive. It will also only scrape videos from folders that have 'CSE' in their name. There are constants defined at the top of panopto.py that can be edited to chage these settings.

The first time you run this, you'll have to authorize access to your Google Drive account. That will open up a browser tab asking you to select the account you want to use and ask you to verify the permissions that are being granted. In this case, we need full Drive permissions because we need to read and write to Drive. Once you've confirmed the access, you'll be good to go. The Google Credentials are also pickled, so future executions won't require you to go through the browser process again.

Once you've authorized the program to access your Google Drive, you can just sit back and let it run. The total runtime will depend primarily on your internet connection -- a 50 minute lecture video is about 500MB.

## Scaling
If your internet connection isn't up to snuff, I'd recommend running this using a cloud-computing service which can offer better speeds. This should run on any system that meet the requirements listed above and that has at least 1GB of memory. I've run this on AWS EC2 without any issues; on a t3a.medium instance, it took about an hour to upload all the videos.

Note that if you run this on a service such as EC2, you should authorize the Google Drive access locally using your browser and simply upload the credentials.json/token.pickle onto the instance.

## Issues
There seems to be a bug in the Python Google Drive that can cause socket timeouts (see [here](https://github.com/googleapis/google-api-python-client/issues/563) for some info). Since I wasn't able to reproduce this, I wasn't able to work around it. If for some reason the program crashes, you can simply restart it and it will pick up where it left off; it'll only upload videos that don't already exist in Google Drive.

## License
[MIT](https://choosealicense.com/licenses/mit/)
